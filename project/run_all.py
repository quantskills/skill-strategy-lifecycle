"""Reproduce the E6 lifecycle study and the three-skill applicability pack."""
from __future__ import annotations

import subprocess
import sys
import json
import os
import time
from pathlib import Path
from project_config import RESULTS, validate_inputs


ROOT = Path(__file__).resolve().parent


steps=[]


def run(script: str, args=()) -> None:
    command = [sys.executable, str(ROOT / script), *args]
    start=time.time()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    directory=RESULTS/'execution_logs'
    directory.mkdir(parents=True,exist_ok=True)
    (directory/(Path(script).stem+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    steps.append({'script':script,'returncode':result.returncode,'seconds':round(time.time()-start,3)})
    (RESULTS/'run_steps.json').write_text(json.dumps(steps,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{script}: '+('完成' if result.returncode==0 else '失败'),flush=True)
    if result.returncode:
        print(result.stderr[-4000:],file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    RESULTS.mkdir(parents=True,exist_ok=True)
    validate_inputs()
    run('tests/run_tests.py')
    run("run_research.py")
    run("scripts/regime_strategy_analysis.py")
    run('extended_validation.py')
    run("verify_e6.py")
    run('build_report_html.py')
    run('acceptance.py')
    run('build_report_html.py')
