"""项目「置顶」标注（project_pin）的测试。

置顶按人隔离（(project_id, operator) 主键），只影响本人的列表顺序；
用假 session 验证 SQL 行为与写入字段，不连库。

运行方式（反射 runner；**必须先 import app.core.db**——conftest 会把 create_engine
换成 MagicMock，若任由 admin 包在之后懒加载 app.core.db，event.listen 会对
MagicMock 引擎报 InvalidRequestError）：
    python -c "
    import app.core.db
    import tests.conftest
    import tests.test_project_pin as t
    import inspect
    n=0
    for name, obj in vars(t).items():
        if inspect.isclass(obj) and name.startswith('Test'):
            inst = obj()
            for m in dir(obj):
                if m.startswith('test_'): getattr(inst, m)(); n+=1
    print('PASS', n)
    "

接口层（401 未登录 / 404 项目不存在 / 幂等重复置顶 / 最近置顶在前）已用临时实例
（uvicorn @8402 + 真库）逐个打通，这里只覆盖服务层逻辑。
"""
from unittest.mock import MagicMock

import app.modules.admin.services.project_pin_service as pin_mod
from app.modules.admin.services.project_pin_service import remove_pins_for_project


class _FakeSessionFactory:
    """把模块级 SessionLocal 换成返回固定假 session 的工厂，用完还原。"""

    def __init__(self, fake_db):
        self.fake_db = fake_db
        self.old = None

    def __enter__(self):
        self.old = pin_mod.SessionLocal
        pin_mod.SessionLocal = lambda: self.fake_db
        return self

    def __exit__(self, *exc):
        pin_mod.SessionLocal = self.old


def _db(tracked=None):
    """假 session：query(...).filter(...).first() 返回 tracked（None = 未置顶）。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = tracked
    return db


class TestPin:
    def test_未置顶时新增一行并用登录名隔离(self):
        db = _db(tracked=None)
        with _FakeSessionFactory(db):
            result = pin_mod.ProjectPinService().pin("p1", "u1", operator_name="张三")

        assert result is True
        added = db.add.call_args[0][0]
        assert added.project_id == "p1"
        assert added.operator == "u1"
        assert added.operator_name == "张三"
        assert added.created_at  # 'YYYY-MM-DD HH:MM:SS'
        db.commit.assert_called_once()

    def test_重复置顶幂等且不刷新置顶时间(self):
        # 已置顶时不新增、不 commit：重复点「置顶」不会把项目顶到最前
        db = _db(tracked="p1")
        with _FakeSessionFactory(db):
            result = pin_mod.ProjectPinService().pin("p1", "u1")

        assert result is True
        db.add.assert_not_called()
        db.commit.assert_not_called()
        db.close.assert_called_once()

    def test_取消置顶按项目与人删除(self):
        db = _db()
        with _FakeSessionFactory(db):
            result = pin_mod.ProjectPinService().unpin("p1", "u1")

        assert result is False
        db.query.return_value.filter.return_value.delete.assert_called_once_with(
            synchronize_session=False,
        )
        db.commit.assert_called_once()


class TestListForOperator:
    def test_按置顶时间倒序返回项目ID(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            ("p2",), ("p1",),
        ]
        with _FakeSessionFactory(db):
            ids = pin_mod.ProjectPinService().list_for_operator("u1")

        assert ids == ["p2", "p1"]
        # 必须排序：列表靠这个顺序决定置顶项目谁在前
        db.query.return_value.filter.return_value.order_by.assert_called_once()


class TestIsPinned:
    def test_查询命中(self):
        db = _db(tracked="p1")
        with _FakeSessionFactory(db):
            assert pin_mod.ProjectPinService().is_pinned("p1", "u1") is True

    def test_未命中(self):
        db = _db(tracked=None)
        with _FakeSessionFactory(db):
            assert pin_mod.ProjectPinService().is_pinned("p1", "u1") is False


class TestRemovePinsForProject:
    def test_项目删除时清掉该项目的全部置顶(self):
        db = MagicMock()
        remove_pins_for_project(db, "p1")
        db.query.return_value.filter.return_value.delete.assert_called_once_with(
            synchronize_session=False,
        )
        db.commit.assert_not_called()  # 与业务同事务，由调用方 commit

    def test_项目ID为空时不查库(self):
        db = MagicMock()
        remove_pins_for_project(db, "")
        db.query.assert_not_called()
