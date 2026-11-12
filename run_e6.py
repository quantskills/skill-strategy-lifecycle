"""EVO 根目录兼容入口：转发到 project/evo_entry.py。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
raise SystemExit(subprocess.call([sys.executable, str(ROOT / "project" / "evo_entry.py")], cwd=ROOT))
