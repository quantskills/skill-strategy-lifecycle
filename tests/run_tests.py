import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from project_config import RESULTS

suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
RESULTS.mkdir(parents=True,exist_ok=True)
(RESULTS/'test_results.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),
    'errors':len(result.errors),'ok':result.wasSuccessful()},indent=2)+'\n',encoding='utf-8')
raise SystemExit(0 if result.wasSuccessful() else 1)
