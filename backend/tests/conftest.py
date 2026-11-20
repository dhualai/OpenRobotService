from unittest.mock import MagicMock
import os, sys, types

import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
_APP = os.path.join(_BACKEND, "app")

# 先桩掉 app.core.database：其模块级 Base.metadata.create_all(bind=engine) 会在
# 导入时真实连库（app/__init__.py 顶部就 import 它）；打桩后导入 app 包不触发建表。
# 用 MagicMock 兜任意属性导入（app.core.auth_routes 等会 from 它 import 各种符号），
# 仅 get_async_db 用真实异步生成器（FastAPI 依赖需要）。
# 这一步必须留在收集期：用例模块在 import 时就 from app... 取符号，fixture 来不及。
if "app.core.database" not in sys.modules:
    _d = MagicMock()
    async def _g(): yield None
    _d.get_async_db = _g
    sys.modules["app.core.database"] = _d

# 必须在改 create_engine 之前导入 app.core.db：它的 engine 挂了 connect 事件监听
# （强制 UTC 会话时区），若 engine 建在桩上，listens_for 会落在 MagicMock 上报
# InvalidRequestError: No such event 'connect'。真实 engine 只建对象不连库（惰性）。
import app.core.db  # noqa: E402,F401
import sqlalchemy as _sa  # noqa: E402
import sqlalchemy.engine  # noqa: E402

for _n, _s in [("app", None), ("app.core", "core")]:
    if _n not in sys.modules:
        _m = types.ModuleType(_n)
        _m.__path__ = [os.path.join(_APP, _s)] if _s else [_APP]
        sys.modules[_n] = _m


@pytest.fixture(autouse=True)
def _mock_create_engine(monkeypatch):
    """把 create_engine 换成 MagicMock，单测不建连接池、不连库。

    以前是在 conftest 导入期直接改写 sqlalchemy.create_engine（全局永久生效）；改成
    用例级 monkeypatch 后，每个用例结束即还原，用例之间不会互相看到被改写的库函数。
    """
    monkeypatch.setattr(_sa, "create_engine", MagicMock())
    monkeypatch.setattr(_sa.engine, "create_engine", MagicMock())
