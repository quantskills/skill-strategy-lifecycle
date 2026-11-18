"""项目配置、Pandadata 运行时输入合同和 E6 内置策略副本。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = json.loads((ROOT / 'project_config.json').read_text(encoding='utf-8'))


def configured_path(variable: str, key: str) -> Path:
    path = Path(os.environ.get(variable, SETTINGS[key])).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


DATA_MODE = os.environ.get('E6_DATA_MODE', 'pandadata').strip().lower()
# Q8 is a logical runtime input namespace.  In the default mode it is created
# by pandadata_runtime.py and is never copied into the repository or package.
Q8 = configured_path('E6_Q8_ROOT', 'q8_root')
RESULTS = configured_path('E6_RESULTS_DIR', 'results_dir')
REQUIRED_Q8 = [
    'scripts/engine_v2.py', 'config/audit_v2.json',
    'data/weekly_slim.csv', 'data/panel_for_platform.csv', 'data/upstream_summary.json',
    'results/v2/backtest_audit.json', 'results/v2/metrics.json',
    'results/v2/monitor.csv', 'results/v2/calibration.json', 'results/v2/health_robustness.json',
]


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def resolve_input(name: str) -> Path:
    """Find a runtime input in plain or common columnar forms."""
    plain = Q8 / name
    if plain.is_file():
        return plain
    packed_stems = [name]
    if name.endswith('.csv'):
        packed_stems.append(name[:-4])
    for suffix in ('.parquet', '.pkl.xz', '.xz', '.gz'):
        for stem in packed_stems:
            packed = Q8 / (stem + suffix)
            if packed.is_file():
                return packed
    return plain


def input_sha256(path: Path) -> str:
    """Hash logical file bytes so compressed delivery equals the frozen CSV hash."""
    return sha256(path)


def validate_inputs() -> dict:
    if DATA_MODE == 'pandadata':
        from pandadata_runtime import prepare_runtime_inputs
        manifest = prepare_runtime_inputs()
        missing = [name for name in REQUIRED_Q8 if not resolve_input(name).is_file()]
        if missing:
            raise FileNotFoundError('Pandadata 运行时输入不完整：' + ', '.join(missing))
        return {'ok': True, 'mode': 'pandadata', **manifest}
    missing = [name for name in REQUIRED_Q8 if not resolve_input(name).is_file()]
    if missing:
        raise FileNotFoundError('缺少冻结 Q8 输入：' + ', '.join(missing) + '; 请设置 E6_Q8_ROOT')
    manifest = json.loads((ROOT / 'frozen_inputs.json').read_text(encoding='utf-8'))
    actual = {name: input_sha256(resolve_input(name)) for name in REQUIRED_Q8}
    changed = [name for name in REQUIRED_Q8 if actual[name] != manifest['sha256'][name]]
    if changed:
        raise ValueError('冻结输入指纹不匹配，停止运行：' + ', '.join(changed))
    return {'ok': True, 'files': len(actual), 'sha256': actual}


def load_q8(name: str = 'q8_frozen_engine'):
    # Load the copied strategy implementation shipped with E6.  Q8 remains
    # the frozen data/calibration source, but E6 no longer imports Q8 code.
    local_engine = ROOT / 'strategy' / 'q8_engine_v2.py'
    spec = importlib.util.spec_from_file_location(name, local_engine)
    if spec is None or spec.loader is None:
        raise ImportError('无法加载 E6 内置策略副本')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Route the copied engine to the externally supplied frozen input assets.
    # This keeps the implementation local to E6 while preserving Q8's exact
    # data and cost configuration.
    module.DATA = Q8 / 'data'
    module.CFG = json.loads((Q8 / 'config' / 'audit_v2.json').read_text(encoding='utf-8'))
    module.OUT = RESULTS
    # Preserve the upstream file unchanged. Only make CSV numeric parsing explicit
    # in the imported module, so fingerprints do not depend on pandas' fast parser.
    original_pd = module.pd
    class RoundTripPandas:
        def __getattr__(self, attr):
            return getattr(original_pd, attr)

        def read_csv(self, *args, **kwargs):
            kwargs.setdefault('float_precision', 'round_trip')
            return original_pd.read_csv(*args, **kwargs)
    module.pd = RoundTripPandas()
    return module
