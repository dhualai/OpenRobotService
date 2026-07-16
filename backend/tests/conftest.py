"""测试共用配置。

项目当前 `app/__init__.py` 在 import 时即 `Base.metadata.create_all` 连 MySQL
（见 CODEBASE_OVERVIEW.md 工程债 #1）。为让单元测试在无 DB 环境下运行，
此处用占位 `app` / `app.models` / `app.core` 包跳过各自 `__init__.py` 的执行；
它们的子模块（app.integrations.* / app.models.task / app.core.config 等）
仍按真实目录路径加载。
"""
import os
import sys
import types

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_BACKEND, "app")

for _name, _sub in (("app", None), ("app.models", "models"), ("app.core", "core")):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        _m.__path__ = [os.path.join(_APP, _sub)] if _sub else [_APP]
        sys.modules[_name] = _m
