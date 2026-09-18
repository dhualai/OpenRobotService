from unittest.mock import MagicMock
import os, sys, types
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
_APP = os.path.join(_BACKEND, "app")

# 先桩掉 app.core.database：其模块级 Base.metadata.create_all(bind=engine) 会在
# 导入时真实连库（app/__init__.py 顶部就 import 它）；打桩后导入 app 包不触发建表。
# 用 MagicMock 兜任意属性导入（app.core.auth_routes 等会 from 它 import 各种符号），
# 仅 get_async_db 用真实异步生成器（FastAPI 依赖需要）。
if "app.core.database" not in sys.modules:
    _d = MagicMock()
    async def _g(): yield None
    _d.get_async_db = _g
    sys.modules["app.core.database"] = _d

# 必须在 mock create_engine 之前导入 app.core.db：它的 engine 挂了 connect 事件监听
# （强制 UTC 会话时区），若先 mock 再导入，listens_for 会落在 MagicMock 上报
# InvalidRequestError: No such event 'connect'。真实 engine 只建对象不连库（惰性），
# 之后再 mock create_engine，让其余模块的 engine 创建照旧走 MagicMock。
import app.core.db  # noqa: E402,F401
import sqlalchemy as _sa
_sa.create_engine = MagicMock()
_sa.engine.create_engine = MagicMock()
for _n, _s in [("app", None), ("app.core", "core")]:
    if _n not in sys.modules:
        _m = types.ModuleType(_n)
        _m.__path__ = [os.path.join(_APP, _s)] if _s else [_APP]
        sys.modules[_n] = _m
