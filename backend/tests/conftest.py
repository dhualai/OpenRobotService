from unittest.mock import MagicMock
import sqlalchemy as _sa
_sa.create_engine = MagicMock()
_sa.engine.create_engine = MagicMock()
import os, sys, types
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_BACKEND, "app")
for _n, _s in [("app", None), ("app.core", "core")]:
    if _n not in sys.modules:
        _m = types.ModuleType(_n)
        _m.__path__ = [os.path.join(_APP, _s)] if _s else [_APP]
        sys.modules[_n] = _m
if "app.core.database" not in sys.modules:
    _d = types.ModuleType("app.core.database")
    _d.db_manager = MagicMock()
    _d.UserDB = MagicMock()
    _d.init_users_db = MagicMock()
    async def _g(): yield None
    _d.get_async_db = _g
    sys.modules["app.core.database"] = _d
