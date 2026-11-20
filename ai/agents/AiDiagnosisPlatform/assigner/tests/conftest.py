"""把仓库根和 backend 加入 sys.path，便于直接跑 assigner 分步单测。"""

import sys
from pathlib import Path
from unittest.mock import patch

_root = Path(__file__).resolve().parents[5]
_backend = _root / "backend"
for p in (str(_root), str(_backend)):
    if p not in sys.path:
        sys.path.insert(0, p)

# backend 的 app.core.database 在导入期就执行 Base.metadata.create_all(bind=engine)，
# 无 MySQL 的环境会直接抛 OperationalError，导致整个目录在收集阶段中断。
# 分步单测的数据访问均已在用例内 patch，不依赖真实库，故在隔离窗口内完成该模块导入。
import sqlalchemy.schema  # noqa: E402

# 桩打在 MetaData 层：app 包初始化链里任何一处 create_all 都会被拦下
with patch.object(sqlalchemy.schema.MetaData, "create_all"):
    import app.core.database  # noqa: E402,F401
