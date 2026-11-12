"""比较迁移复现结果、审计指纹及输入失败边界；不启动运行平台。"""
import argparse
import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from project_config import ROOT,RESULTS as OUT,REQUIRED_Q8,sha256


def main(other: Path, interpreter: Path):
    other=other.resolve();interpreter=interpreter.resolve();checks={};maxdiff=0.
    for name in ['summary.csv','state_ledger.csv','state_attribution.csv','state_transitions.csv','ablation.csv','parameter_sensitivity.csv','execution_stress.csv','secondary_signal_summary.csv']:
        a=pd.read_csv(OUT/name);b=pd.read_csv(other/'results'/name)
        ok=a.shape==b.shape and a.columns.tolist()==b.columns.tolist()
        if ok:
            for c in a:
                if c=='phase_totals':
                    ok=ok and all(np.allclose(json.loads(x),json.loads(y),atol=1e-12,rtol=1e-12) for x,y in zip(a[c],b[c]))
                elif pd.api.types.is_numeric_dtype(a[c]) and pd.api.types.is_numeric_dtype(b[c]):
                    ok=ok and bool(np.allclose(a[c],b[c],atol=1e-12,rtol=1e-12,equal_nan=True))
                    d=np.abs(a[c].to_numpy(dtype=float)-b[c].to_numpy(dtype=float))
                    if np.isfinite(d).any():maxdiff=max(maxdiff,float(np.nanmax(d)))
                else:ok=ok and a[c].fillna('<NA>').astype(str).equals(b[c].fillna('<NA>').astype(str))
        checks[name]=bool(ok)
    source_files=['run_all.py','run_research.py','state_machine.py','inference.py','extended_validation.py','project_config.py','evo_entry.py','acceptance.py','build_report_html.py']
    source_match={n:sha256(ROOT/n)==sha256(other/n) for n in source_files}
    deps=subprocess.run([str(interpreter),'-m','pip','freeze','--local'],capture_output=True,text=True,check=True).stdout.splitlines()
    with tempfile.TemporaryDirectory(prefix='e6_bad_inputs_') as temporary:
        fake=Path(temporary);env={**os.environ,'E6_Q8_ROOT':str(fake),'PYTHONIOENCODING':'utf-8'}
        command=[str(interpreter),'-c','from project_config import validate_inputs;validate_inputs()']
        missing=subprocess.run(command,cwd=other,env=env,capture_output=True,text=True,encoding='utf-8')
        for n in REQUIRED_Q8:
            p=fake/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('changed',encoding='utf-8')
        changed=subprocess.run(command,cwd=other,env=env,capture_output=True,text=True,encoding='utf-8')
    output=json.loads((other/'runs/evo/output.json').read_text(encoding='utf-8'))
    snapshot=ROOT/'runs/compatibility'
    snapshot.mkdir(parents=True,exist_ok=True)
    for name in ['input.json','output.json']:
        shutil.copy2(other/'runs/evo'/name,snapshot/name)
    (snapshot/'results').mkdir(exist_ok=True)
    for name in output['output_sha256']:
        shutil.copy2(other/'results'/name,snapshot/'results'/name)
    result={'scope':'local_migration_and_clean_dependencies_only',
        'ok':all(checks.values()) and all(source_match.values()) and output['returncode']==0 and missing.returncode!=0 and changed.returncode!=0,
        'migrated_directory':str(other),'independent_environment':True,'dependencies':deps,'entry_returncode':output['returncode'],
        'table_values_match_within_1e_12':checks,'source_sha256_matches':source_match,'max_numeric_difference':maxdiff,
        'missing_inputs_rejected':missing.returncode!=0,'tampered_fingerprints_rejected':changed.returncode!=0,
        'target_platform_execution_claimed':False,
        'input_record':'runs/compatibility/input.json',
        'output_record':'runs/compatibility/output.json',
        'tested_output_hashes_verified':all(sha256(snapshot/'results'/n)==digest for n,digest in output['output_sha256'].items())}
    result['ok']=result['ok'] and result['tested_output_hashes_verified']
    (OUT/'portability_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['ok']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--other',type=Path,required=True);parser.add_argument('--python',type=Path,required=True)
    args=parser.parse_args();main(args.other,args.python)
