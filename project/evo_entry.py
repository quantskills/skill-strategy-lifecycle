"""EVO 可运行入口：调用 E6 研究主流程并保存运行记录。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from project_config import ROOT, RESULTS, sha256, validate_inputs


record_dir = ROOT / "runs" / "evo"
record_dir.mkdir(parents=True, exist_ok=True)
meta = {
    "entry_script": "evo_entry.py",
    "research_entry": "run_all.py",
    "started_at_epoch": time.time(),
    "execution_scope": "project_entry; caller_environment_not_asserted",
    "python_version": sys.version.split()[0],
    "plan_sha256": sha256(ROOT/'analysis_plan.json'),
    "entry_sha256": sha256(Path(__file__)),
}
(record_dir / "input.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
print("EVO_ENTRY_START " + json.dumps(meta, ensure_ascii=False), flush=True)
try:
    validate_inputs()
    completed = subprocess.run([sys.executable, str(ROOT / "run_all.py")], cwd=ROOT, check=False)
    code=completed.returncode
    error=None
except Exception as exc:
    code=1
    error=str(exc)
result = {**meta, "returncode": code, "finished_at_epoch": time.time(), "error":error,
          "status":"succeeded" if code==0 else "failed",
          "output_sha256":{name:sha256(RESULTS/name) for name in ['summary.csv','state_ledger.csv','inference_v2.json','report.html','acceptance.json'] if (RESULTS/name).exists()}}
(record_dir / "output.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print("EVO_ENTRY_FINISH " + json.dumps(result, ensure_ascii=False), flush=True)
raise SystemExit(code)

