"""项目配置与冻结 Q8 输入合同；不依赖运行平台的内部实现。"""
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


def validate_inputs() -> dict:
    missing = [name for name in REQUIRED_Q8 if not (Q8 / name).is_file()]
    if missing:
        raise FileNotFoundError('缺少冻结 Q8 输入：' + ', '.join(missing) + '; 请设置 E6_Q8_ROOT')
    manifest = json.loads((ROOT / 'frozen_inputs.json').read_text(encoding='utf-8'))
    actual = {name: sha256(Q8 / name) for name in REQUIRED_Q8}
    changed = [name for name in REQUIRED_Q8 if actual[name] != manifest['sha256'][name]]
    if changed:
        raise ValueError('冻结输入指纹不匹配，停止运行：' + ', '.join(changed))
    return {'ok': True, 'files': len(actual), 'sha256': actual}


def load_q8(name: str = 'q8_frozen_engine'):
    spec = importlib.util.spec_from_file_location(name, Q8 / 'scripts/engine_v2.py')
    if spec is None or spec.loader is None:
        raise ImportError('无法加载冻结 Q8 研究引擎')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
