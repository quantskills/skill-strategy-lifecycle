"""生成自包含研究运行包；只复制本项目和冻结 Q8 研究输入。"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from project_config import ROOT, Q8, REQUIRED_Q8, sha256, validate_inputs


def build(destination: Path, include_results: bool = False):
    validate_inputs()
    destination=destination.resolve()
    if destination.exists():raise FileExistsError('不覆盖已有交付目录：'+str(destination))
    destination.mkdir(parents=True)
    for pattern in ['*.py','*.json','*.md','requirements*.txt']:
        for source in ROOT.glob(pattern):shutil.copy2(source,destination/source.name)
    for name in ['scripts','tests']:
        shutil.copytree(ROOT/name,destination/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(ROOT/'strategy',destination/'strategy',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['skill-factor-health-monitor','agent-market-regime-monitor','skill-quant-strategy-diagnostics']:
        source=ROOT/'.agents/skills'/name
        shutil.copytree(source,destination/'.agents/skills'/name,ignore=shutil.ignore_patterns('__pycache__','.git','*.pyc','.env'))
    for name in REQUIRED_Q8:
        target=destination/'frozen_q8'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Q8/name,target)
    if include_results:
        shutil.copytree(ROOT/'results',destination/'results',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        if (ROOT/'runs/compatibility').exists():
            shutil.copytree(ROOT/'runs/compatibility',destination/'runs/compatibility')
        if (ROOT/'runs/before_v2').exists():
            shutil.copytree(ROOT/'runs/before_v2',destination/'runs/before_v2',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    config=json.loads((destination/'project_config.json').read_text(encoding='utf-8'))
    config['q8_root']='frozen_q8'
    (destination/'project_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest={str(p.relative_to(destination)).replace('\\','/'):sha256(p) for p in destination.rglob('*') if p.is_file()}
    (destination/'delivery_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'destination':str(destination),'files':len(manifest),'frozen_inputs':len(REQUIRED_Q8)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--include-results',action='store_true')
    args=parser.parse_args();build(args.destination,args.include_results)
