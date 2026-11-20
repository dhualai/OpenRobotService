"""禅道适配器单元测试：parse_project_ids 与 is_enabled。

注意：import 本模块会触发 app.integrations.sources.zentao 包自注册
（registry 多出 'zentao'），本测试不依赖 registry 的全局空态，故无影响。
"""
import pytest

from app.core.config import settings
from app.integrations.sources.zentao.adapter import ZentaoAdapter, parse_project_ids


@pytest.mark.parametrize("raw,expected", [
    ("[1,2,3]", [1, 2, 3]),
    ("1,2,3", [1, 2, 3]),
    ("1;2;3", [1, 2, 3]),
    ("1 2 3", [1, 2, 3]),
    ("[5]", [5]),
    ("42", [42]),
    ("", []),
    ("   ", []),
    ("[1, 2, 3]", [1, 2, 3]),
    ("not-a-list", []),           # 整体非法 → 容错为空
    ("1,abc,3", [1, 3]),          # 部分非法 → 过滤掉非法片段
])
def test_parse_project_ids(raw, expected):
    assert parse_project_ids(raw) == expected


def _configure(monkeypatch, **overrides):
    base = dict(
        ZENTAO_BASE_URL="http://zentao.example.com",
        ZENTAO_ACCOUNT="account",
        ZENTAO_PASSWORD="password",
        ZENTAO_PROJECT_IDS="[1,2,3]",
    )
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setattr(settings, k, v)


def test_is_enabled_true_when_fully_configured(monkeypatch):
    _configure(monkeypatch)
    assert ZentaoAdapter().is_enabled() is True


@pytest.mark.parametrize("missing", [
    "ZENTAO_BASE_URL", "ZENTAO_ACCOUNT", "ZENTAO_PASSWORD", "ZENTAO_PROJECT_IDS",
])
def test_is_enabled_false_when_any_missing(monkeypatch, missing):
    _configure(monkeypatch, **{missing: ""})
    assert ZentaoAdapter().is_enabled() is False


def test_is_enabled_false_when_project_ids_unparseable(monkeypatch):
    _configure(monkeypatch, ZENTAO_PROJECT_IDS="not-a-list")
    # 非数字、非 JSON、非分隔符 → 解析为空 → 未启用
    assert ZentaoAdapter().is_enabled() is False
