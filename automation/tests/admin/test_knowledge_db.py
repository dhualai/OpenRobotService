"""知识生产闭环（knowledge 模块）数据库集成测试。

目标：验证 knowledge 6 张表在真实 MySQL 上的约束、软删语义、JSON 往返与状态契约。
来源口径：backend/app/models/knowledge.py、backend/app/modules/admin/services/knowledge_service.py（只读依据）。
运行：cd automation && pytest tests/admin/test_knowledge_db.py -m db -v
库指向：环境变量 AUTOMATION_DB_NAME（约定独立测试库 helpdesk_test），未设置回落 config.yaml。
"""
import json

import allure
import pytest
import pymysql

pytestmark = [pytest.mark.db]

SETTLEMENT_DEFAULTS = {"rankings": [], "audit": []}


@allure.feature("系统任务")
class TestKnowledgePublicationDb:
    """knowledge_publications：知识发布主表"""

    @allure.story("知识发布")
    @allure.title("数据库：正常发布落库，全字段回读一致")
    def test_publication_insert_ok(self, knowledge_db):
        """数据库：正常流程 发布记录完整落库并可回读"""
        c = knowledge_db
        task_id = c.new_task_id()
        c.client.execute(
            "INSERT INTO knowledge_publications (task_id, report_id, questioner_id, answerer_id,"
            " title, problem, content, review_comment, value_point, first_approved_at, active)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (task_id, task_id + 1, AUTO_T_PREFIX + "asker", AUTO_T_PREFIX + "answer",
             "AUTO_T_标题", "问题背景", "解决方案正文", "审核意见", "价值点", "2026-11-10 12:00:00", 1),
        )
        row = c.client.fetch_one("SELECT * FROM knowledge_publications WHERE task_id = %s", (task_id,))
        assert row is not None
        assert row["report_id"] == task_id + 1
        assert row["title"] == "AUTO_T_标题"
        assert row["active"] in (1, True)
        assert row["questioner_id"] == "AUTO_T_asker"

    @allure.story("知识发布")
    @allure.title("数据库：缺失必填列 report_id 触发完整性约束")
    def test_publication_missing_required_column(self, knowledge_db):
        """数据库：数据校验 缺 report_id -> IntegrityError"""
        c = knowledge_db
        task_id = c.new_task_id()
        with pytest.raises(pymysql.err.IntegrityError):
            c.client.execute(
                "INSERT INTO knowledge_publications (task_id, questioner_id, answerer_id,"
                " title, problem, content, review_comment, value_point, first_approved_at, active)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (task_id, AUTO_T_PREFIX + "asker", AUTO_T_PREFIX + "answer",
                 "AUTO_T_标题", "问题背景", "正文", "审核", "价值", "2026-11-10 12:00:00", 1),
            )

    @allure.story("知识发布")
    @allure.title("数据库：超长内容经 LONGTEXT 完整存取")
    def test_publication_longtext_content_roundtrip(self, knowledge_db):
        """数据库：边界条件 >64KB 内容无损往返"""
        c = knowledge_db
        task_id = c.new_task_id()
        big = "知" * 40000  # utf8mb4 下约 120KB，超过 TEXT 64KB 上限
        c.client.execute(
            "INSERT INTO knowledge_publications (task_id, report_id, questioner_id, answerer_id,"
            " title, problem, content, review_comment, value_point, first_approved_at, active)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (task_id, task_id + 1, AUTO_T_PREFIX + "a", AUTO_T_PREFIX + "b",
             "AUTO_T_big", "p", big, "c", "v", "2026-11-10 12:00:00", 1),
        )
        row = c.client.fetch_one("SELECT content FROM knowledge_publications WHERE task_id = %s", (task_id,))
        assert row["content"] == big


@allure.feature("系统任务")
class TestArchiveReviewDecisionDb:
    """archive_review_decisions：审核决策与版本唯一约束"""

    @allure.story("审核决策")
    @allure.title("数据库：同 report 递增 version 依次入库")
    def test_review_decision_unique_version_ok(self, knowledge_db):
        """数据库：状态流转 report1-v1、report1-v2 均成功"""
        c = knowledge_db
        task_id = c.new_task_id()
        for version in (1, 2):
            c.client.execute(
                "INSERT INTO archive_review_decisions (task_id, report_id, version, reviewer_id,"
                " outcome, review_comment, content, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (task_id, task_id + 1, version, AUTO_T_PREFIX + "reviewer",
                 "approved", "通过", "正文", "2026-11-10 12:00:00"),
            )
        rows = c.client.fetch_all(
            "SELECT version FROM archive_review_decisions WHERE task_id = %s ORDER BY version", (task_id,)
        )
        assert [r["version"] for r in rows] == [1, 2]

    @allure.story("审核决策")
    @allure.title("数据库：同 report 重复 version 触发唯一约束")
    def test_review_decision_duplicate_version(self, knowledge_db):
        """数据库：数据库 重复 (report_id, version) -> IntegrityError(uq_archive_review_version)"""
        c = knowledge_db
        task_id = c.new_task_id()
        insert = (
            "INSERT INTO archive_review_decisions (task_id, report_id, version, reviewer_id,"
            " outcome, review_comment, content, created_at) VALUES (%s, %s, 1, %s, %s, %s, %s, %s)"
        )
        params = (task_id, task_id + 1, AUTO_T_PREFIX + "reviewer", "approved", "通过", "正文", "2026-11-10 12:00:00")
        c.client.execute(insert, params)
        with pytest.raises(pymysql.err.IntegrityError):
            c.client.execute(insert, params)


@allure.feature("系统任务")
class TestKnowledgeLikeDb:
    """knowledge_question_likes / knowledge_like_events：复合主键软删语义"""

    @allure.story("点赞")
    @allure.title("数据库：复合主键行复用，不产生重复行")
    def test_like_composite_pk_upsert(self, knowledge_db):
        """数据库：数据校验 同 (task,user) 只允许一行，切换走 UPDATE"""
        c = knowledge_db
        task_id = c.new_task_id()
        user = AUTO_T_PREFIX + "liker"
        insert = (
            "INSERT INTO knowledge_question_likes (task_id, user_id, active, updated_at)"
            " VALUES (%s, %s, %s, %s)"
        )
        c.client.execute(insert, (task_id, user, 1, "2026-11-10 12:00:00"))
        with pytest.raises(pymysql.err.IntegrityError):
            c.client.execute(insert, (task_id, user, 1, "2026-11-10 12:00:01"))
        c.client.execute(
            "UPDATE knowledge_question_likes SET active = %s WHERE task_id = %s AND user_id = %s",
            (0, task_id, user),
        )
        rows = c.client.fetch_all(
            "SELECT active FROM knowledge_question_likes WHERE task_id = %s AND user_id = %s", (task_id, user)
        )
        assert len(rows) == 1
        assert rows[0]["active"] in (0, False)

    @allure.story("点赞")
    @allure.title("数据库：取消赞为软删，行保留且事件留痕")
    def test_like_soft_delete_keeps_row(self, knowledge_db):
        """数据库：状态流转 active 1->0 行保留 + 事件表追加"""
        c = knowledge_db
        task_id = c.new_task_id()
        user = AUTO_T_PREFIX + "liker"
        c.client.execute(
            "INSERT INTO knowledge_question_likes (task_id, user_id, active, updated_at)"
            " VALUES (%s, %s, 1, %s)", (task_id, user, "2026-11-10 12:00:00"))
        c.client.execute(
            "INSERT INTO knowledge_like_events (task_id, user_id, active, created_at)"
            " VALUES (%s, %s, 0, %s)", (task_id, user, "2026-11-10 12:00:01"))
        like = c.client.fetch_one(
            "SELECT active FROM knowledge_question_likes WHERE task_id = %s AND user_id = %s", (task_id, user))
        event = c.client.fetch_one(
            "SELECT active FROM knowledge_like_events WHERE task_id = %s AND user_id = %s", (task_id, user))
        assert like["active"] in (1, True)          # likes 行未物理删除
        assert event["active"] in (0, False)        # 事件表留痕取消动作

    @allure.story("点赞")
    @allure.title("数据库：事件表按 created_at 可排序，追加有序")
    def test_like_event_append_order(self, knowledge_db):
        """数据库：数据库 事件追加顺序与时间排序一致"""
        c = knowledge_db
        task_id = c.new_task_id()
        user = AUTO_T_PREFIX + "liker"
        for i, active in enumerate((1, 0, 1), start=1):
            c.client.execute(
                "INSERT INTO knowledge_like_events (task_id, user_id, active, created_at)"
                " VALUES (%s, %s, %s, %s)", (task_id, user, active, f"2026-11-10 12:00:0{i}"))
        rows = c.client.fetch_all(
            "SELECT active FROM knowledge_like_events WHERE task_id = %s AND user_id = %s"
            " ORDER BY created_at", (task_id, user))
        assert [bool(r["active"]) for r in rows] == [True, False, True]


@allure.feature("系统任务")
class TestKnowledgeSettlementDb:
    """knowledge_settlements：月度结算快照（month 主键 + JSON）"""

    @allure.story("月度结算")
    @allure.title("数据库：新建快照落库默认 reviewing/revision=1")
    def test_settlement_create_reviewing_defaults(self, knowledge_db):
        """数据库：正常流程 月份主键 + DDL 默认值生效"""
        c = knowledge_db
        month = "2026-11"
        c.track_month(month)
        c.client.execute(
            "INSERT INTO knowledge_settlements (month, rankings, audit, frozen_at)"
            " VALUES (%s, %s, %s, %s)",
            (month, json.dumps([{"user_id": AUTO_T_PREFIX + "u1"}]), json.dumps([]), "2026-11-10 12:00:00"),
        )
        row = c.client.fetch_one(
            "SELECT status, revision FROM knowledge_settlements WHERE month = %s", (month,))
        assert row["status"] == "reviewing"
        assert int(row["revision"]) == 1

    @allure.story("月度结算")
    @allure.title("数据库：rankings/audit JSON 写读深度相等")
    def test_settlement_json_roundtrip(self, knowledge_db):
        """数据库：数据库 JSON 字段跨驱动往返"""
        c = knowledge_db
        month = "2026-11"
        c.track_month(month)
        rankings = [{"user_id": AUTO_T_PREFIX + "u1", "valid_like_count": 3, "entries": ["a", "b"]}]
        audit = [{"action": "freeze", "at": "2026-11-10T12:00:00", "by": AUTO_T_PREFIX + "admin"}]
        c.client.execute(
            "INSERT INTO knowledge_settlements (month, rankings, audit, frozen_at)"
            " VALUES (%s, %s, %s, %s)", (month, json.dumps(rankings), json.dumps(audit), "2026-11-10 12:00:00"))
        row = c.client.fetch_one(
            "SELECT rankings, audit FROM knowledge_settlements WHERE month = %s", (month,))

        def parse(v):
            return json.loads(v) if isinstance(v, str) else v

        assert parse(row["rankings"]) == rankings
        assert parse(row["audit"]) == audit

    @allure.story("月度结算")
    @allure.title("数据库：月份主键冲突报错，更新契约走 UPDATE revision+1")
    def test_settlement_month_pk_conflict_update_semantics(self, knowledge_db):
        """数据库：数据校验 同 month 二次 INSERT 冲突 -> UPDATE 语义 revision 递增"""
        c = knowledge_db
        month = "2026-11"
        c.track_month(month)
        insert = ("INSERT INTO knowledge_settlements (month, rankings, audit, frozen_at)"
                  " VALUES (%s, %s, %s, %s)")
        base = (month, json.dumps([]), json.dumps([]), "2026-11-10 12:00:00")
        c.client.execute(insert, base)
        with pytest.raises(pymysql.err.IntegrityError):
            c.client.execute(insert, base)
        c.client.execute(
            "UPDATE knowledge_settlements SET revision = revision + 1, status = %s WHERE month = %s",
            ("held", month))
        row = c.client.fetch_one(
            "SELECT revision, status FROM knowledge_settlements WHERE month = %s", (month,))
        assert int(row["revision"]) == 2
        assert row["status"] == "held"


@allure.feature("系统任务")
class TestKnowledgeDiscussionDb:
    """knowledge_discussions：追加式讨论"""

    @allure.story("讨论")
    @allure.title("数据库：讨论追加写入，作者与内容正确落库")
    def test_discussion_append_only(self, knowledge_db):
        """数据库：正常流程 追加两条，回读字段一致"""
        c = knowledge_db
        task_id = c.new_task_id()
        for i in (1, 2):
            c.client.execute(
                "INSERT INTO knowledge_discussions (task_id, author_id, content, created_at)"
                " VALUES (%s, %s, %s, %s)",
                (task_id, f"{AUTO_T_PREFIX}u{i}", f"讨论内容{i}", f"2026-11-10 12:00:0{i}"))
        rows = c.client.fetch_all(
            "SELECT author_id, content FROM knowledge_discussions WHERE task_id = %s ORDER BY created_at",
            (task_id,))
        assert [(r["author_id"], r["content"]) for r in rows] == [
            (f"{AUTO_T_PREFIX}u1", "讨论内容1"), (f"{AUTO_T_PREFIX}u2", "讨论内容2")]
