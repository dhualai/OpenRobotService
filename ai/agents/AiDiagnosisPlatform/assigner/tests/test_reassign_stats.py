"""转派统计：三个固定信号、重新派单拆开、错派率分母不含未标注。"""

from ai.agents.AiDiagnosisPlatform.assigner.sync.reassign_stats import (
    aggregate_events,
    correction_pair,
    event_channel,
    parse_reason_comment,
    _apply_comment_reasons,
    _unlabeled_groups,
    _unlabeled_items,
)


class TestEventChannel:
    def test_user_kind_is_signal(self):
        """正常流程：弹窗点选的 kind 就是转派信号。"""
        assert event_channel({"kind": "misassign", "new_assignee": "u2"}) == "signal"
        assert event_channel({"kind": "stage"}) == "signal"
        assert event_channel({"kind": "other"}) == "signal"

    def test_redispatch_preferred(self):
        """正常流程：重新派单写 preferred_assignee，单独通道。"""
        assert event_channel({"preferred_assignee": "张三", "remark": "再派一次"}) == "redispatch"

    def test_redispatch_stays_channel_after_verdict(self):
        """正常流程：审核算不准确后仍是重新派单通道，不混进弹窗信号。"""
        assert event_channel({
            "preferred_assignee": "u1",
            "redispatch_verdict": "inaccurate",
            "kind_source": "review",
        }) == "redispatch"

    def test_redispatch_description(self):
        """正常流程：旧重新派单靠描述识别。"""
        assert event_channel({}, "李四 重新派单，倾向处理人 张三") == "redispatch"

    def test_unlabeled_old_transfer(self):
        """边界：旧转派只有 new_assignee，没有 kind。"""
        assert event_channel({"new_assignee": "u2"}) == "unlabeled"

    def test_resume_without_kind(self):
        """边界：继续处理改人没有 kind，不算其它。"""
        assert event_channel({"new_assignee": "u2", "from_assignee": "u1"}) == "unlabeled"


    def test_skipped_not_in_queue(self):
        """正常流程：审核点跳过，不再出现在未标注里。"""
        assert event_channel({"kind_source": "skipped", "new_assignee": "u2"}) == "skipped"

    def test_review_kind_is_signal(self):
        """正常流程：人工审核写入的 kind 和弹窗点选一样算信号。"""
        assert event_channel({"kind": "misassign", "kind_source": "review"}) == "signal"


class TestCorrectionPair:
    def test_popup_misassign(self):
        """正常流程：弹窗派错了抽出 A→B。"""
        assert correction_pair({
            "kind": "misassign", "from_assignee": "u-a", "new_assignee": "u-b",
            "reason": "不归硬件",
        }) == ("u-a", "u-b", "不归硬件")

    def test_stage_not_learned(self):
        """正常流程：阶段转派不学。"""
        assert correction_pair({
            "kind": "stage", "from_assignee": "u-a", "new_assignee": "u-b",
        }) is None

    def test_redispatch_inaccurate(self):
        """正常流程：审核不准确的重新派单也学，压原处理人。"""
        assert correction_pair({
            "channel": "redispatch",
            "from_assignee": "u-a",
            "preferred_assignee": "u-b",
            "redispatch_verdict": "inaccurate",
            "remark": "再派一次",
        }, "重新派单") == ("u-a", "u-b", "再派一次")

    def test_redispatch_pending_and_skipped_not_learned(self):
        """边界：待审和测试不算不进学习。"""
        pending = {
            "channel": "redispatch",
            "from_assignee": "u-a",
            "preferred_assignee": "u-b",
        }
        skipped = {**pending, "redispatch_verdict": "skipped"}
        assert correction_pair(pending) is None
        assert correction_pair(skipped) is None

    def test_redispatch_without_b_still_penalizes_a(self):
        """边界：没有倾向人时仍压原处理人。"""
        assert correction_pair({
            "channel": "redispatch",
            "from_assignee": "u-a",
            "redispatch_verdict": "inaccurate",
        }) == ("u-a", "", "")


class TestAggregate:
    def test_rates_only_from_signal(self):
        """正常流程：错派率分母是有类型的转派，重新派单和未标注不进。"""
        events = [
            {"kind": "misassign", "channel": "signal", "task_id": 1},
            {"kind": "stage", "channel": "signal", "task_id": 2},
            {"kind": "", "channel": "unlabeled", "task_id": 3},
            {"kind": "", "channel": "redispatch", "task_id": 4},
            {"kind": "", "channel": "skipped", "task_id": 5},
        ]
        m = aggregate_events(events, ai_assign_total=10, ai_assign_tickets=8)
        assert m["signal_total"] == 2
        assert m["unlabeled_total"] == 1
        assert m["redispatch_total"] == 1
        assert m["skipped_total"] == 1
        assert m["misassign_rate_of_signal"] == 0.5
        assert m["misassign_rate_of_ai_assign"] == 0.1
        assert m["by_kind"]["stage"] == 1
        assert m["by_kind"]["other"] == 0

    def test_reviewed_redispatch_counts_inaccurate(self):
        """正常流程：重新派单标成不准确后计入不准确，不进弹窗错派率分母。"""
        events = [
            {"kind": "misassign", "channel": "signal", "task_id": 1},
            {"kind": "", "channel": "redispatch", "task_id": 2,
             "redispatch_verdict": "inaccurate",
             "detail": {"preferred_assignee": "u", "redispatch_verdict": "inaccurate"}},
            {"kind": "", "channel": "redispatch", "task_id": 3,
             "redispatch_verdict": "skipped",
             "detail": {"preferred_assignee": "u", "redispatch_verdict": "skipped"}},
            {"kind": "", "channel": "redispatch", "task_id": 4,
             "detail": {"preferred_assignee": "u"}},
        ]
        m = aggregate_events(events, ai_assign_total=10, ai_assign_tickets=8)
        assert m["redispatch_total"] == 3
        assert m["redispatch_inaccurate"] == 1
        assert m["redispatch_skipped"] == 1
        assert m["redispatch_pending"] == 1
        assert m["misassign_events"] == 1
        assert m["inaccurate_events"] == 2
        assert m["misassign_rate_of_signal"] == 1.0
        assert m["redispatch_inaccurate_rate_of_reviewed"] == 0.5
        assert m["inaccurate_rate_of_ai_assign"] == 0.2


class TestUnlabeledGroups:
    def test_same_ticket_keeps_every_unlabeled_hop(self):
        """正常流程：一张单多次未标转派全部进审核，不只最后一次。"""
        events = [
            {"id": 3, "task_id": 10, "title": "电机过热", "channel": "unlabeled",
             "kind": "", "reason": "", "description": "转给王五",
             "created_at": "2026-09-09T12:00:00",
             "detail": {"from_assignee": "u2", "new_assignee": "u3"}},
            {"id": 2, "task_id": 10, "title": "电机过热", "channel": "unlabeled",
             "kind": "", "reason": "", "description": "转给李四",
             "created_at": "2026-09-09T11:00:00",
             "detail": {"from_assignee": "u1", "new_assignee": "u2"}},
            {"id": 1, "task_id": 10, "title": "电机过热", "channel": "signal",
             "kind": "stage", "reason": "", "description": "转给张三",
             "created_at": "2026-09-09T10:00:00",
             "detail": {"from_assignee": "u0", "new_assignee": "u1", "kind": "stage"}},
        ]
        names = {"u0": "零号", "u1": "张三", "u2": "李四", "u3": "王五"}
        groups = _unlabeled_groups(events, names)
        assert len(groups) == 1
        hops = groups[0]["hops"]
        assert len(hops) == 3
        assert [h["reviewable"] for h in hops] == [False, True, True]
        assert [h["to_name"] for h in hops] == ["张三", "李四", "王五"]
        items = _unlabeled_items(events, names)
        assert [i["id"] for i in items] == [2, 3]

    def test_fill_from_previous_hop(self):
        """边界：旧日志没有 from_assignee 时，用上一跳的接手人补上。"""
        events = [
            {"id": 2, "task_id": 8, "title": "旧单", "channel": "unlabeled",
             "kind": "", "reason": "", "description": "第二次",
             "created_at": "2026-09-09T11:00:00",
             "detail": {"new_assignee": "u2"}},
            {"id": 1, "task_id": 8, "title": "旧单", "channel": "unlabeled",
             "kind": "", "reason": "", "description": "第一次",
             "created_at": "2026-09-09T10:00:00",
             "detail": {"new_assignee": "u1"}},
        ]
        groups = _unlabeled_groups(events, {"u1": "甲", "u2": "乙"})
        hops = groups[0]["hops"]
        assert hops[0]["from_name"] == "—"
        assert hops[0]["to_name"] == "甲"
        assert hops[1]["from_name"] == "甲"
        assert hops[1]["to_name"] == "乙"


class TestReasonComment:
    def test_parse_old_required_reason(self):
        """正常流程：旧转派原因写在评论前缀里。"""
        assert parse_reason_comment("重新指派原因：不归硬件，应派软件") == "不归硬件，应派软件"
        assert parse_reason_comment("重新指派原因:现场已换人") == "现场已换人"

    def test_apply_comment_to_matching_hop(self):
        """正常流程：审核列表用评论补上日志里没有的原因。"""
        hops = [
            {"id": 1, "created_at": "2026-09-09T10:00:00", "reason": ""},
            {"id": 2, "created_at": "2026-09-09T11:00:00", "reason": ""},
        ]
        _apply_comment_reasons(hops, [
            {"created_at": "2026-09-09T10:00:02", "reason": "第一次转走"},
            {"created_at": "2026-09-09T11:00:03", "reason": "第二次转走"},
        ])
        assert hops[0]["reason"] == "第一次转走"
        assert hops[1]["reason"] == "第二次转走"

    def test_keep_log_reason(self):
        """边界：日志里已有 reason 时不要被评论覆盖。"""
        hops = [{"id": 1, "created_at": "2026-09-09T10:00:00", "reason": "日志里的原因"}]
        _apply_comment_reasons(hops, [
            {"created_at": "2026-09-09T10:00:02", "reason": "评论里的原因"},
        ])
        assert hops[0]["reason"] == "日志里的原因"


class TestClusterParams:
    def test_normalize_ok(self):
        """正常流程：开发者模式写入的簇门槛落在合法区间。"""
        from ai.agents.AiDiagnosisPlatform.assigner.settings import normalize_cluster_params
        out = normalize_cluster_params(
            cluster_merge=0.4, cluster_assign=0.35, cluster_min_size=5,
        )
        assert out["cluster_merge"] == 0.4
        assert out["cluster_assign"] == 0.35
        assert out["cluster_min_size"] == 5

    def test_normalize_rejects_out_of_range(self):
        """异常流程：门槛过高过低都拒绝。"""
        from ai.agents.AiDiagnosisPlatform.assigner.settings import normalize_cluster_params
        import pytest
        with pytest.raises(ValueError):
            normalize_cluster_params(cluster_merge=0.05)
        with pytest.raises(ValueError):
            normalize_cluster_params(cluster_assign=1.5)
        with pytest.raises(ValueError):
            normalize_cluster_params(cluster_min_size=99)

    def test_normalize_skips_empty_min_size(self):
        """数据校验：最小团空/0 不挡合并门槛保存。"""
        from ai.agents.AiDiagnosisPlatform.assigner.settings import normalize_cluster_params
        out = normalize_cluster_params(cluster_merge=0.4, cluster_min_size=0)
        assert out == {"cluster_merge": 0.4}


class TestEmbedPath:
    def test_resolve_uses_local_when_server_missing(self, tmp_path):
        """正常流程：服务器 Linux 路径不存在时用 EMBEDDING_MODEL_LOCAL。"""
        from pathlib import Path
        from ai.core.embed import resolve_embed_model_path
        server = tmp_path / "missing" / "bge-base-zh-v1.5"
        local = tmp_path / "local" / "bge-base-zh-v1.5"
        local.mkdir(parents=True)
        (local / "config.json").write_text("{}", encoding="utf-8")
        (local / "pytorch_model.bin").write_bytes(b"x")
        out = resolve_embed_model_path(str(server), str(local))
        assert Path(out) == local.resolve()

    def test_resolve_skips_empty_stub_dir(self, tmp_path):
        """异常流程：只有空目录不算可用模型，继续报找不到。"""
        from ai.core.embed import resolve_embed_model_path
        from ai.exceptions import EmbeddingError
        import pytest
        stub = tmp_path / "bge-base-zh-v1.5"
        stub.mkdir()
        (stub / "1_Pooling").mkdir()
        with pytest.raises(EmbeddingError, match="找不到 embedding 目录"):
            resolve_embed_model_path("/data/apps/missing/bge-other-zh-v1.5", str(stub))
