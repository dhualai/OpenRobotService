"""项目工单卡服务测试：周趋势分桶 / 默认阻滞排序 / 提示词组装 / AI 结果解析 / 阻滞板块取数。

纯函数不连库；_load_blocking 用假 AsyncSession 验证取数与降级行为；LLM 不真实调用
（configure_blocking 的空提示词校验在发请求前完成）。
运行方式（反射 runner；**必须先 import app.core.db**——conftest 会把 create_engine
换成 MagicMock，若任由 admin 包在之后懒加载 app.core.db，event.listen 会对
MagicMock 引擎报 InvalidRequestError）：
    python -c "
    import app.core.db
    import tests.conftest
    import tests.test_project_tickets as t
    import inspect
    n=0
    for name, obj in vars(t).items():
        if inspect.isclass(obj) and name.startswith('Test'):
            inst = obj()
            for m in dir(obj):
                if m.startswith('test_'): getattr(inst, m)(); n+=1
    print('PASS', n)
    "
"""
import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

from app.models.task import Task, TaskPriority, TaskStatus, TaskType
from app.modules.admin.services.project_tickets_service import (
    AI_BLOCKING_LIMIT,
    ProjectTicketsService,
    build_blocking_prompt,
    build_weekly_trend,
    default_blocking_order,
    parse_blocking_result,
    week_start_of,
)


def _run(coro):
    return asyncio.run(coro)


def _expect_error(exc_type, coro_fn):
    """反射 runner 不 await，异步用例由 asyncio.run 驱动；返回捕获到的异常供断言。"""
    try:
        _run(coro_fn())
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__} to be raised")


def _task(task_id=1, *, status=TaskStatus.IN_PROGRESS, priority=TaskPriority.MEDIUM,
          created_at=None, deadline_at=None, title=None, project_id="P1",
          ticket_type=TaskType.PROBLEM, description=""):
    """与 Task ORM 属性同名的轻量替身（服务只读这些属性）。"""
    return SimpleNamespace(
        id=task_id,
        title=title or f"工单{task_id}",
        status=status,
        priority=priority,
        task_type=ticket_type,
        created_by="user-a",
        assigned_to="u1",
        created_at=created_at,
        deadline_at=deadline_at,
        description=description,
        project_id=project_id,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _FakeDb:
    """按 execute 调用顺序返回预设结果；get 返回预设配置行。"""

    def __init__(self, exec_results=None, config=None):
        self.exec_results = list(exec_results or [])
        self.config = config
        self.added = []

    async def execute(self, query):
        return _FakeResult(self.exec_results.pop(0))

    async def get(self, model, pk):
        return self.config

    def add(self, row):
        self.added.append(row)


class TestWeekStartOf:
    def test_周三是本周一_截断时分秒(self):
        assert week_start_of(datetime(2026, 9, 16, 15, 30, 45)) == datetime(2026, 9, 14)

    def test_周一当天是自身零点(self):
        assert week_start_of(datetime(2026, 9, 14, 8, 0, 0)) == datetime(2026, 9, 14)

    def test_周日归到本周一(self):
        assert week_start_of(datetime(2026, 9, 20, 23, 59)) == datetime(2026, 9, 14)


class TestBuildWeeklyTrend:
    NOW = datetime(2026, 9, 16, 12, 0, 0)  # 周三，本周起点 2026-09-14

    def test_按周分桶且横轴完整(self):
        created = [
            datetime(2026, 9, 16, 9, 0),   # 本周
            datetime(2026, 9, 15, 9, 0),   # 本周
            datetime(2026, 9, 8, 9, 0),    # 上周
            datetime(2026, 7, 27, 9, 0),   # 8 周窗口的最早一周
        ]
        trend = build_weekly_trend(created, self.NOW)
        assert len(trend) == 8
        assert trend[-1] == {"week_start": "2026-09-14", "count": 2}
        assert trend[-2] == {"week_start": "2026-09-07", "count": 1}
        assert trend[0] == {"week_start": "2026-07-27", "count": 1}
        # 中间无记录的周计 0，保持横轴完整
        assert trend[1]["count"] == 0

    def test_窗口外与空值忽略(self):
        created = [datetime(2026, 7, 20, 9, 0), None, datetime(2026, 9, 14, 0, 0)]
        trend = build_weekly_trend(created, self.NOW)
        assert trend[0]["week_start"] == "2026-07-27"
        assert sum(item["count"] for item in trend) == 1


class TestDefaultBlockingOrder:
    def test_优先级优先于超期(self):
        urgent_late = _task(1, priority=TaskPriority.URGENT, deadline_at=datetime(2026, 9, 30))
        high_overdue = _task(2, priority=TaskPriority.HIGH, deadline_at=datetime(2026, 1, 1))
        order = default_blocking_order([high_overdue, urgent_late])
        assert [t.id for t in order] == [1, 2]

    def test_同优先级超期最久在前_无截止置后(self):
        no_deadline = _task(1, deadline_at=None)
        later = _task(2, deadline_at=datetime(2026, 9, 20))
        earlier = _task(3, deadline_at=datetime(2026, 9, 1))
        order = default_blocking_order([no_deadline, later, earlier])
        assert [t.id for t in order] == [3, 2, 1]

    def test_同级同截止最新创建在前(self):
        older = _task(1, deadline_at=None, created_at=datetime(2026, 9, 1))
        newer = _task(2, deadline_at=None, created_at=datetime(2026, 9, 10))
        order = default_blocking_order([older, newer])
        assert [t.id for t in order] == [2, 1]


class TestBuildBlockingPrompt:
    PROJECT = {"name": "郑州项目", "project_code": "ZZ-1", "status": "正在实施",
               "description": "", "special_attention": "客户催验收"}

    def _ticket(self, **overrides):
        base = {
            "id": 12, "title": "导航不识别货架", "status": "in_progress", "status_label": "处理中",
            "priority": "high", "priority_label": "高", "ticket_type": "bug", "type_label": "缺陷",
            "created_at": "2026-09-01", "deadline_at": "2026-09-10", "overdue": True,
            "creator_name": "张三", "assignee_name": "李四", "description": "现场多台车复现",
        }
        base.update(overrides)
        return base

    def test_包含管理员的判定要求与项目字段(self):
        prompt = build_blocking_prompt(self.PROJECT, [self._ticket()], "优先考虑影响验收的工单")
        assert "优先考虑影响验收的工单" in prompt
        assert "项目名称：郑州项目" in prompt
        assert "客户催验收" in prompt
        # 空字段不出现占位
        assert "项目描述" not in prompt

    def test_工单行带标签与超期标记(self):
        prompt = build_blocking_prompt(self.PROJECT, [self._ticket()], "x")
        assert "#12" in prompt
        assert "[状态=处理中]" in prompt
        assert "[优先级=高]" in prompt
        assert "[类型=缺陷]" in prompt
        assert "（已超期）" in prompt
        assert "提单人=张三" in prompt
        assert "描述：现场多台车复现" in prompt

    def test_无工单时给出占位(self):
        prompt = build_blocking_prompt(self.PROJECT, [], "x")
        assert "（暂无工单）" in prompt
        assert "共 0 条" in prompt


class TestParseBlockingResult:
    def test_正常解析并保序去重过滤(self):
        text = json.dumps({
            "ticket_ids": [12, "7", 12, 99, "abc"],
            "summary": " 现场问题集中 ",
            "reasons": {"12": " 影响验收 ", "7": ""},
        })
        parsed = parse_blocking_result(text, {12, 7})
        assert parsed["ticket_ids"] == [12, 7]
        assert parsed["summary"] == "现场问题集中"
        # 空理由剔除，保留有效理由
        assert parsed["reasons"] == {"12": "影响验收"}

    def test_剥离代码块围栏与前后杂文(self):
        text = "好的，结果如下：\n```json\n{\"ticket_ids\": [3], \"summary\": \"s\", \"reasons\": {}}\n```\n以上。"
        parsed = parse_blocking_result(text, {3})
        assert parsed["ticket_ids"] == [3]

    def test_超出上限截断(self):
        ids = list(range(1, 10))
        text = json.dumps({"ticket_ids": ids, "summary": "", "reasons": {}})
        parsed = parse_blocking_result(text, set(ids))
        assert len(parsed["ticket_ids"]) == AI_BLOCKING_LIMIT
        assert parsed["ticket_ids"] == ids[:AI_BLOCKING_LIMIT]

    def test_无有效工单报错(self):
        text = json.dumps({"ticket_ids": [99], "summary": "s"})
        _expect_error(RuntimeError, lambda: parse_blocking_result(text, {1}))

    def test_非JSON报错(self):
        _expect_error(RuntimeError, lambda: parse_blocking_result("模型今天不想说话", {1}))

    def test_JSON数组而非对象报错(self):
        _expect_error(RuntimeError, lambda: parse_blocking_result("[1, 2]", {1}))


class TestLoadBlocking:
    def _service(self):
        return ProjectTicketsService()

    def _config(self, ai_result):
        return SimpleNamespace(
            project_id="P1",
            prompt="优先验收相关",
            ai_result=ai_result,
            updated_by="admin",
            updated_by_name="管理员",
            updated_at="2026-09-16 12:00:00",
        )

    def test_已配置时按存储顺序回查_缺失工单剔除(self):
        config = self._config(json.dumps({
            "ticket_ids": [7, 3, 99],  # 99 已被删除
            "summary": "两单阻滞",
            "reasons": {"7": "阻塞验收"},
        }))
        tasks = [_task(3, title="第二重要"), _task(7, title="最重要")]
        db = _FakeDb(exec_results=[tasks], config=config)  # 第一个 execute 是回查工单

        blocking = _run(self._service()._load_blocking(db, "P1", {}))
        assert blocking["mode"] == "ai"
        assert [t["id"] for t in blocking["tickets"]] == [7, 3]  # 按存储顺序，缺失剔除
        assert blocking["summary"] == "两单阻滞"
        assert blocking["reasons"] == {"7": "阻塞验收"}
        assert blocking["prompt"] == "优先验收相关"
        assert blocking["updated_by_name"] == "管理员"

    def test_选出的工单已不属于该项目时剔除(self):
        config = self._config(json.dumps({"ticket_ids": [7], "summary": "", "reasons": {}}))
        moved = _task(7, project_id="OTHER")
        default_rows = [_task(2, status=TaskStatus.PENDING)]
        db = _FakeDb(exec_results=[[moved], default_rows], config=config)

        blocking = _run(self._service()._load_blocking(db, "P1", {}))
        # AI 选中的工单绑到了别的项目：退回默认规则
        assert blocking["mode"] == "default"
        assert [t["id"] for t in blocking["tickets"]] == [2]

    def test_配置损坏时退回默认规则_保留提示词(self):
        config = self._config("{bad json")
        default_rows = [
            _task(1, priority=TaskPriority.LOW, deadline_at=datetime(2026, 9, 1)),
            _task(2, priority=TaskPriority.URGENT, deadline_at=None),
        ]
        db = _FakeDb(exec_results=[default_rows], config=config)

        blocking = _run(self._service()._load_blocking(db, "P1", {}))
        assert blocking["mode"] == "default"
        assert [t["id"] for t in blocking["tickets"]] == [2, 1]
        assert blocking["prompt"] == "优先验收相关"  # 管理员可在此基础上改提示词

    def test_未配置时默认规则取前三(self):
        rows = [_task(i, priority=TaskPriority.LOW, deadline_at=datetime(2026, 9, i + 1)) for i in range(1, 6)]
        db = _FakeDb(exec_results=[rows], config=None)

        blocking = _run(self._service()._load_blocking(db, "P1", {}))
        assert blocking["mode"] == "default"
        assert len(blocking["tickets"]) == 3
        # 超期最久（deadline 最早）在前
        assert [t["id"] for t in blocking["tickets"]] == [1, 2, 3]
        assert blocking["prompt"] is None
        # 条目带问题概况摘要与人员名兜底
        first = blocking["tickets"][0]
        assert first["creator_name"] == "user-a"
        assert first["assignee_name"] == "u1"


class TestConfigureBlockingValidation:
    def test_空提示词直接拒绝(self):
        _expect_error(ValueError, lambda: ProjectTicketsService().configure_blocking(None, "P1", "   "))


class TestTicketItemPreview:
    def test_描述摘要截断(self):
        task = _task(1, description=" 多   空格 " + "长描述" * 60)
        item = ProjectTicketsService()._ticket_item(task, {})
        assert item["description"].endswith("…")
        assert "  " not in item["description"]
        assert len(item["description"]) == 81  # 80 字 + 省略号
