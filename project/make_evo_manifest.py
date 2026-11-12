"""生成项目侧产物清单；不包含运行平台信息。"""
import json
from project_config import ROOT,RESULTS,sha256

if __name__=='__main__':
    manifest={str(p.relative_to(RESULTS)).replace('\\','/'):sha256(p) for p in RESULTS.rglob('*') if p.is_file()}
    path=ROOT/'runs/project_outputs_manifest.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'scope':'project_outputs_only','files':manifest},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(path)
