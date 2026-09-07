"""把仓库根和 backend 加入 sys.path，便于直接跑 assigner 分步单测。"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[5]
_backend = _root / "backend"
for p in (str(_root), str(_backend)):
    if p not in sys.path:
        sys.path.insert(0, p)
