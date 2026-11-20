"""Worker P0：同单 inflight 互斥、写库 CAS 决策、结单增量索引。

不连 Redis / MySQL / Qdrant。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_worker.py -v
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ai.agents.AiDiagnosisPlatform.assigner.pipeline.worker import AssignmentWorker
from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import (
    build_index_record,
    index_history_records,
)


def _ticket(task_id: int = 7) -> dict:
    return {"id": task_id, "title": "电机过热", "description": "现场报障"}


def _result(**kwargs):
    data = dict(
        engineer_id="u1",
        engineer_name="张三",
        confidence_score=0.9,
        decision_type="ranked",
        reasoning="ok",
        profile={},
        candidates=[],
        preferred_id=None,
        matched_pref=False,
        name_collision=False,
        pinyin_match=False,
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


class TestInflight:
    def test_second_channel_skips_llm(self):
        """正常流程：Pub/Sub 与 poll 同时捡到同一张单，只跑一遍派单。"""

        async def _run():
            worker = AssignmentWorker()
            worker._inflight.add(7)
            with patch(
                "ai.agents.AiDiagnosisPlatform.assigner.pipeline.worker.assign_ticket",
                new_callable=AsyncMock,
            ) as mocked:
                await worker._assign_one(_ticket(7))
                mocked.assert_not_called()

        asyncio.run(_run())

    def test_inflight_released_after_assign(self):
        """正常流程：派单结束后释放 inflight，后续扫描可以再进。"""

        async def _run():
            worker = AssignmentWorker()
            with patch.object(worker, "_assign_one_locked", new_callable=AsyncMock) as locked:
                await worker._assign_one(_ticket(8))
                locked.assert_awaited_once()
            assert 8 not in worker._inflight

        asyncio.run(_run())


class TestCasWrite:
    def test_empty_assigned_to_is_unassigned(self):
        """数据校验：None 和空串都算未指派，有人则不算。"""
        assert AssignmentWorker._is_unassigned(None)
        assert AssignmentWorker._is_unassigned("")
        assert not AssignmentWorker._is_unassigned("u1")

    def test_cas_loss_does_not_write_log(self):
        """异常流程：CAS 抢不到行 → 不写 dispatch_log、不发通知侧写回。"""
        db = MagicMock()
        task = SimpleNamespace(id=1, assigned_to=None)
        db.query.return_value.filter.return_value.first.return_value = task

        with patch(
            "app.core.db.SessionLocal",
            return_value=db,
        ), patch(
            "app.models.task.Task",
            MagicMock(),
        ), patch(
            "app.models.task.TaskOperationLog",
            MagicMock(),
        ), patch(
            "app.models.task.OperationType",
            MagicMock(),
        ), patch(
            "app.models.task_dispatch_log.TaskDispatchLog",
            MagicMock(),
        ), patch.object(AssignmentWorker, "_claim_unassigned", return_value=0):
            ok = AssignmentWorker._update_task_assignee(1, _result())

        assert ok is False
        db.add.assert_not_called()
        db.commit.assert_not_called()
        db.rollback.assert_called()

    def test_unassignable_does_not_clear_existing_assignee(self):
        """异常流程：另一路已派人时，unassignable 不得把 assigned_to 清掉。"""
        db = MagicMock()
        task = SimpleNamespace(id=2, assigned_to="u-other")
        db.query.return_value.filter.return_value.first.return_value = task

        with patch("app.core.db.SessionLocal", return_value=db), patch(
            "app.models.task.Task", MagicMock()
        ), patch(
            "app.models.task.TaskOperationLog", MagicMock()
        ), patch(
            "app.models.task.OperationType", MagicMock()
        ), patch(
            "app.models.task_dispatch_log.TaskDispatchLog", MagicMock()
        ):
            ok = AssignmentWorker._update_task_assignee(
                2,
                _result(engineer_id=None, profile={"unassignable": True}),
            )

        assert ok is False
        db.add.assert_not_called()
        db.commit.assert_not_called()


class TestClosedIndex:
    def test_first_lookback_is_two_hours(self):
        """正常流程：重启后首次增量向前看约 2 小时。"""
        worker = AssignmentWorker(interval=60)
        since = worker._index_lookback_since()
        delta = datetime.now() - since
        assert timedelta(hours=1, minutes=50) < delta < timedelta(hours=2, minutes=10)

    def test_build_index_record_uses_ticket_id(self):
        """正常流程：同一 ticket_id 收成稳定字段，供 Qdrant 覆盖写。"""
        rec = build_index_record(
            ticket_id=12,
            engineer_id="u1",
            title="电机过热报警",
            description="现场无法复位",
            task_type="problem",
            metadata={"fault_code": "E1001", "robot_type": "X1"},
            finished_at=None,
            keyword_dict={"底盘": ["电机"]},
        )
        assert rec["ticket_id"] == 12
        assert rec["engineer_id"] == "u1"
        assert rec["fault_code"] == "E1001"
        assert rec["robot_type"] == "X1"
        assert "底盘" in rec["modules"]

    def test_index_history_records_empty(self):
        """正常流程：窗口内无结单则不打检索服务。"""

        async def _run():
            out = await index_history_records([])
            assert out == {"total": 0, "indexed": 0, "skipped": 0, "collection": ""}

        asyncio.run(_run())

    def test_index_failure_keeps_watermark(self):
        """异常流程：Qdrant 全失败时不推进水位，下轮还能扫到这批单。"""

        async def _run():
            worker = AssignmentWorker(interval=60)
            rec = [{"ticket_id": 1, "engineer_id": "u1", "title": "a",
                    "description": "b", "modules": [], "task_type": "problem",
                    "fault_code": "", "robot_type": "", "closed_at": ""}]
            with patch.object(worker, "_load_recently_finished", return_value=rec), patch(
                "ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer.index_history_records",
                new_callable=AsyncMock,
                return_value={"total": 1, "indexed": 0, "skipped": 1, "collection": ""},
            ):
                await worker._scan_and_index_closed()
            assert worker._index_since is None

        asyncio.run(_run())


class TestLearnWindow:
    def test_learn_at_picks_old_log(self):
        """正常流程：旧重新派单审核后靠 learn_at 进入本轮学习窗口。"""
        from datetime import timezone
        from ai.agents.AiDiagnosisPlatform.assigner.pipeline.worker import _in_learn_window

        since = datetime(2026, 9, 8, 12, 0, 0)
        created = datetime(2026, 9, 1, 8, 0, 0)
        learn_at = datetime(2026, 9, 8, 12, 30, 0, tzinfo=timezone.utc).isoformat()
        assert _in_learn_window(created, learn_at, since) is True
        assert _in_learn_window(created, None, since) is False
        assert _in_learn_window(datetime(2026, 9, 8, 13, 0, 0), None, since) is True
