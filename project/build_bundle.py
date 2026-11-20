"""生成 Pandadata 运行包，并附带最小 Q8 A 组历史标签与对账证据。"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from project_config import ROOT, sha256


DATA_NAMES = {"frozen_q8", "runtime_data", "results", "runs", "tmp_evo_platform"}


def _copy_tree(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".env", "outputs"))


def build(destination: Path, include_results: bool = False):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("不覆盖已有交付目录：" + str(destination))
    destination.mkdir(parents=True)

    for pattern in ["*.py", "*.json", "*.md", "requirements*.txt"]:
        for source in ROOT.glob(pattern):
            if source.name == "frozen_inputs.json":
                continue
            shutil.copy2(source, destination / source.name)
    for name in ["scripts", "tests", "strategy"]:
        _copy_tree(ROOT / name, destination / name)
    skills = ROOT / ".agents" / "skills"
    for name in ["skill-factor-health-monitor", "agent-market-regime-monitor", "skill-quant-strategy-diagnostics"]:
        _copy_tree(skills / name, destination / ".agents" / "skills" / name)
    # 技能示例中的 CSV 不是本项目运行输入，交付包也不保留示例数据。
    examples_data = destination / ".agents" / "skills" / "skill-quant-strategy-diagnostics" / "examples" / "data"
    if examples_data.exists():
        shutil.rmtree(examples_data)

    # 交付目录把 project/ 内容平铺到根目录，因此生成一个适配平铺结构的入口。
    (destination / "run_e6.py").write_text(
        "from __future__ import annotations\n"
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "raise SystemExit(subprocess.call([sys.executable, str(ROOT / 'evo_entry.py')], cwd=ROOT))\n",
        encoding="utf-8",
    )
    (destination / "README.md").write_text(
        "# E6 策略生命周期研究\n\n"
        "本包默认在 EVO 运行时从 Pandadata 获取新研究输入。根目录入口为 `python run_e6.py`。\n\n"
        "运行前在 EVO 数据连接中授权 Pandadata，或提供 `PANDADATA_USERNAME`、"
        "`PANDADATA_PASSWORD`、`PANDADATA_BASE_URL`。按需设置 `PANDADATA_START_DATE`、"
        "`PANDADATA_END_DATE`、`PANDADATA_UNIVERSE` 和 `PANDADATA_FACTORS`。\n\n"
        "包内仅附有历史 Q8 A 组的 `date,symbol,fwd_ret` 标签，用于固定基线口径。"
        "逐项交付要求与验收状态见 `references/q8-a-baseline/requirement_acceptance.md`，"
        "标签规则、源文件指纹和 88 日期收益对账见 `references/q8-a-baseline/`。"
        "标签不是行情表或因子面板，也不是本次 Pandadata 运行结果。\n\n"
        "运行产物写入 `results/`，输入和输出回执写入 `runs/evo/`。\n",
        encoding="utf-8",
    )
    references = ROOT.parent / "references"
    if references.is_dir():
        _copy_tree(references, destination / "references")

    config = json.loads((destination / "project_config.json").read_text(encoding="utf-8"))
    config.update({"q8_root": "runtime_data/q8", "data_mode": "pandadata", "data_in_package": False})
    (destination / "project_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {str(p.relative_to(destination)).replace("\\", "/"): sha256(p)
                for p in destination.rglob("*") if p.is_file()}
    delivery = {"package_version": "pandadata-runtime-v8-q8a-reconciliation", "data_in_package": True,
                "included_data": ["references/q8-a-baseline/weekly_labels_date_symbol_fwd_ret.csv.gz"],
                "market_data_in_package": False, "factor_panel_in_package": False,
                "runtime_data_source": "Pandadata", "entry": "run_e6.py 或 project/evo_entry.py",
                "credentials": ["PANDADATA_USERNAME", "PANDADATA_PASSWORD", "PANDADATA_BASE_URL"],
                "runtime_environment": ["E6_DATA_MODE=pandadata", "PANDADATA_START_DATE", "PANDADATA_END_DATE",
                                         "PANDADATA_UNIVERSE", "PANDADATA_FACTORS"],
                "excluded_paths": sorted(DATA_NAMES), "files": manifest}
    (destination / "delivery_manifest.json").write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"destination": str(destination), "files": len(manifest), "data_in_package": True,
                      "included_data": delivery["included_data"],
                      "runtime_source": "Pandadata"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--include-results", action="store_true", help="兼容旧命令；新包仍不会带研究结果数据")
    args = parser.parse_args()
    build(args.destination, args.include_results)
