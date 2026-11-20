"""项目主数据角色：与讨论区 @ 置顶同源，不走现场档案。"""

from ai.agents.AiTaskPlatform.contexts.contexts import asks_project_role, format_project_roles


def test_empty_project_id_returns_empty():
    assert format_project_roles("") == ""
    assert format_project_roles("   ") == ""


def test_asks_project_role():
    assert asks_project_role("本项目对接人是谁")
    assert asks_project_role("项目经理是谁")
    assert not asks_project_role("这台车是什么车型")


def test_format_project_roles_uses_contact_person(monkeypatch):
    class _Row:
        contact_person = "李金喜"
        project_contact = ""
        contact_person_id = ""
        project_manager = "张三"
        project_manager_id = ""
        sales = "何乐伟"
        pre_sales = ""
        field_engineer = ""

    class _Q:
        def filter(self, *_a, **_k):
            return self

        def first(self):
            return _Row()

    class _Db:
        def query(self, *_a, **_k):
            return _Q()

        def close(self):
            return None

    monkeypatch.setattr(
        "ai.core.database.SessionLocal",
        lambda: _Db(),
    )
    text = format_project_roles("P001")
    assert "项目主数据角色" in text
    assert "项目对接人: 李金喜" in text
    assert "项目经理: 张三" in text
    assert "销售: 何乐伟" in text


def test_format_project_roles_resolves_contact_person_id(monkeypatch):
    class _Project:
        contact_person = ""
        project_contact = ""
        contact_person_id = "u-ljx"
        project_manager = ""
        project_manager_id = ""
        sales = ""
        pre_sales = ""
        field_engineer = ""

    class _User:
        id = "u-ljx"
        username = "lijinxi"
        name = "李金喜"

    class _Q:
        def __init__(self, model):
            self._model = model

        def filter(self, *_a, **_k):
            return self

        def first(self):
            name = getattr(self._model, "__name__", "")
            if name == "User":
                return _User()
            return _Project()

    class _Db:
        def query(self, model, *_a, **_k):
            return _Q(model)

        def close(self):
            return None

    monkeypatch.setattr(
        "ai.core.database.SessionLocal",
        lambda: _Db(),
    )
    text = format_project_roles("P001")
    assert "项目对接人: 李金喜" in text
