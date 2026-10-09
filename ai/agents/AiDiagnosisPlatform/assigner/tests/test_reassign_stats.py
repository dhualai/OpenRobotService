"""转派统计：三个固定信号、重新派单拆开、错派率分母不含未标注。"""

from ai.agents.AiDiagnosisPlatform.assigner.sync.reassign_stats import (
    aggregate_events,
    build_dispatch_funnel,
    build_funnel_weekly,
    build_ticket_lists,
    build_weekly_metrics,
    classify_dispatch_branch,
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

    def test_redispatch_scheme_a_auto_learned(self):
        """方案 A：默认重派（非×2）可学，不必再点审核。"""
        assert correction_pair({
            "channel": "redispatch",
            "from_assignee": "u-a",
            "preferred_assignee": "u-b",
            "kind_source": "scheme_a_auto",
            "redispatch_verdict": "inaccurate",
        }) == ("u-a", "u-b", "")

    def test_redispatch_preferred_twice_not_learned(self):
        """方案 A：倾向人×2 不进学习。"""
        assert correction_pair({
            "channel": "redispatch",
            "from_assignee": "u-a",
            "preferred_assignee": "u-a",
            "preferred_twice_confirm": True,
        }) is None

    def test_redispatch_pending_and_skipped_not_learned(self):
        """边界：无倾向人的 pending 不学；测试不算不学。"""
        pending = {
            "channel": "redispatch",
            "from_assignee": "u-a",
        }
        skipped = {
            "channel": "redispatch",
            "from_assignee": "u-a",
            "preferred_assignee": "u-b",
            "redispatch_verdict": "skipped",
        }
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
        """方案 A：有倾向人的重派默认不准确；测试不算 / ×2 除外。"""
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
            {"kind": "", "channel": "redispatch", "task_id": 5,
             "detail": {"preferred_assignee": "u", "preferred_twice_confirm": True}},
        ]
        m = aggregate_events(events, ai_assign_total=10, ai_assign_tickets=8)
        assert m["redispatch_total"] == 4
        assert m["redispatch_inaccurate"] == 2  # task 2 + task 4 默认
        assert m["redispatch_skipped"] == 1
        assert m["redispatch_preferred_twice"] == 1
        assert m["redispatch_pending"] == 0
        assert m["misassign_events"] == 1
        assert m["inaccurate_events"] == 3
        assert m["misassign_rate_of_signal"] == 1.0
        assert m["inaccurate_rate_of_ai_assign"] == 0.3


class TestTicketListsAndWeekly:
    def test_ticket_lists_dedupe_latest(self):
        """正常流程：错派清单按工单去重，保留最新一条。"""
        events = [
            {"task_id": 1, "title": "新", "channel": "signal", "kind": "misassign",
             "created_at": "2026-09-09T12:00:00", "reason": "第二次"},
            {"task_id": 1, "title": "旧", "channel": "signal", "kind": "misassign",
             "created_at": "2026-09-08T12:00:00", "reason": "第一次"},
            {"task_id": 2, "title": "重派坏", "channel": "redispatch", "kind": "",
             "redispatch_verdict": "inaccurate", "created_at": "2026-09-09T10:00:00",
             "detail": {"redispatch_verdict": "inaccurate"}, "reason": ""},
            {"task_id": 3, "title": "阶段", "channel": "signal", "kind": "stage",
             "created_at": "2026-09-09T09:00:00", "reason": ""},
        ]
        lists = build_ticket_lists(events)
        assert [x["task_id"] for x in lists["misassign"]] == [1]
        assert lists["misassign"][0]["title"] == "新"
        assert [x["task_id"] for x in lists["redispatch_inaccurate"]] == [2]
        assert {x["task_id"] for x in lists["inaccurate"]} == {1, 2}
        assert {x["task_id"] for x in lists["signal"]} == {1, 3}

    def test_weekly_metrics_split_by_week(self):
        """正常流程：两周各自算错派率，不混总盘。"""
        events = [
            {"task_id": 1, "channel": "signal", "kind": "misassign",
             "created_at": "2026-09-08T10:00:00", "detail": {}},  # 周二 W37
            {"task_id": 2, "channel": "signal", "kind": "stage",
             "created_at": "2026-09-08T11:00:00", "detail": {}},
            {"task_id": 3, "channel": "signal", "kind": "misassign",
             "created_at": "2026-09-15T10:00:00", "detail": {}},  # 下一周
        ]
        ai_rows = [
            {"task_id": 10, "created_at": "2026-09-08T09:00:00"},
            {"task_id": 11, "created_at": "2026-09-08T09:30:00"},
            {"task_id": 12, "created_at": "2026-09-15T09:00:00"},
        ]
        weekly = build_weekly_metrics(events, ai_rows, keep=8)
        assert len(weekly) == 2
        assert weekly[0]["metrics"]["misassign_events"] == 1
        assert weekly[0]["metrics"]["signal_total"] == 2
        assert weekly[0]["metrics"]["misassign_rate_of_signal"] == 0.5
        assert weekly[0]["metrics"]["ai_assign_total"] == 2
        assert weekly[1]["metrics"]["misassign_events"] == 1
        assert weekly[1]["metrics"]["signal_total"] == 1
        assert weekly[1]["metrics"]["misassign_rate_of_signal"] == 1.0
        assert weekly[1]["metrics"]["ai_assign_total"] == 1


class TestDispatchFunnel:
    def test_classify_step0_and_preferred_twice(self):
        """正常流程：Step0 / 倾向人×2 从派单日志字段区分。"""
        assert classify_dispatch_branch(
            matched_pref=True,
            preferred_id="u1",
            assigned_id="u1",
            reasoning="提单人指定: 张三 → 张三",
            profile={"specified_name": "张三"},
        ) == "step0"
        assert classify_dispatch_branch(
            matched_pref=True,
            preferred_id="u1",
            assigned_id="u1",
            reasoning="用户连续两次选择倾向处理人 张三，按指定无条件指派",
            profile={},
        ) == "preferred_twice"
        assert classify_dispatch_branch(
            matched_pref=True,
            preferred_id="u1",
            assigned_id="u1",
            reasoning="综合画像与相似单决定派给张三",
            profile={},
        ) == ""

    def test_funnel_excludes_step0_shows_overlap(self):
        """正常流程：扣 Step0；倾向人仅曝光；两错派分支与重合分开。"""
        tickets = [
            {"task_id": 1, "title": "指定单", "created_at": "2026-09-15T10:00:00"},
            {"task_id": 2, "title": "AI 单", "created_at": "2026-09-15T11:00:00"},
            {"task_id": 3, "title": "重合单", "created_at": "2026-09-15T12:00:00"},
            {"task_id": 4, "title": "人工单", "created_at": "2026-09-15T13:00:00"},
            {"task_id": 5, "title": "倾向×2", "created_at": "2026-09-15T14:00:00"},
        ]
        flags = {
            1: {"step0": True, "preferred_twice": False, "preferred_twice_attempts": 0},
            2: {"step0": False, "preferred_twice": False, "preferred_twice_attempts": 0},
            3: {"step0": False, "preferred_twice": False, "preferred_twice_attempts": 0},
            4: {"step0": False, "preferred_twice": False, "preferred_twice_attempts": 0},
            5: {"step0": False, "preferred_twice": True, "preferred_twice_attempts": 2},
        }
        ai_rows = [
            {"task_id": 1, "created_at": "2026-09-15T10:05:00"},
            {"task_id": 2, "created_at": "2026-09-15T11:05:00"},
            {"task_id": 3, "created_at": "2026-09-15T12:05:00"},
            {"task_id": 3, "created_at": "2026-09-15T12:10:00"},
            {"task_id": 5, "created_at": "2026-09-15T14:05:00"},
            {"task_id": 5, "created_at": "2026-09-15T14:10:00"},
        ]
        events = [
            {"task_id": 2, "channel": "signal", "kind": "misassign", "detail": {"kind": "misassign"}},
            {"task_id": 3, "channel": "signal", "kind": "misassign", "detail": {"kind": "misassign"}},
            {
                "task_id": 3, "channel": "redispatch", "kind": "",
                "redispatch_verdict": "inaccurate",
                "detail": {"preferred_assignee": "u", "redispatch_verdict": "inaccurate"},
            },
            # Step0 单上的错派不进 AI 池分子
            {"task_id": 1, "channel": "signal", "kind": "misassign", "detail": {"kind": "misassign"}},
        ]
        funnel = build_dispatch_funnel(tickets, ai_rows, events, flags)
        assert funnel["created_total"] == 5
        assert funnel["drops"]["never_ai"]["count"] == 1
        assert funnel["drops"]["never_ai"]["status"] == "deduct"
        assert funnel["drops"]["never_ai"]["order"] == 1
        assert funnel["drops"]["step0"]["count"] == 1
        assert funnel["drops"]["step0"]["status"] == "deduct"
        assert funnel["drops"]["step0"]["order"] == 2
        assert funnel["drops"]["preferred_twice"]["count"] == 1
        assert funnel["drops"]["preferred_twice"]["status"] == "expose"
        assert funnel["drops"]["preferred_twice"]["attempts"] == 2
        # 先扣从未 AI → 4；再扣 Step0 → AI 池 3（2,3,5）
        assert funnel["ticket_funnel"]["after_never_ai"] == 4
        assert funnel["ticket_funnel"]["after_step0"] == 3
        assert funnel["ticket_funnel"]["ai_pool"] == 3
        assert funnel["ticket_funnel"]["misassign_only"] == 1
        assert funnel["ticket_funnel"]["redispatch_only"] == 0
        assert funnel["ticket_funnel"]["both"] == 1
        assert funnel["ticket_funnel"]["union"] == 2
        assert funnel["attempt_funnel"]["never_ai_attempts"] == 1
        assert funnel["attempt_funnel"]["created_attempts"] == 7  # 走过 AI 6 次 + 未走 AI 1 次
        assert funnel["attempt_funnel"]["after_never_ai_attempts"] == 6
        assert funnel["attempt_funnel"]["ai_assign_total"] == 5  # 2+2+1，不含 step0 的 1
        assert funnel["attempt_funnel"]["step0_attempts"] == 1
        assert funnel["attempt_funnel"]["misassign_events"] == 2
        assert funnel["attempt_funnel"]["redispatch_inaccurate_events"] == 1
        assert funnel["rates"]["ticket_union"] == round(2 / 3, 4)
        assert funnel["rates"]["attempt_union"] == round(3 / 5, 4)

    def test_weekly_attempts_follow_dispatch_time(self):
        """正常流程：按次归到派单发生周，按单仍归工单创建周。"""
        tickets = [
            {"task_id": 1, "title": "上周一创建", "created_at": "2026-09-15T10:00:00"},
            {"task_id": 2, "title": "本周未走 AI", "created_at": "2026-09-22T10:00:00"},
            {"task_id": 3, "title": "Step0", "created_at": "2026-09-10T10:00:00"},
        ]
        flags = {
            1: {"step0": False, "preferred_twice": False, "preferred_twice_attempts": 0},
            2: {"step0": False, "preferred_twice": False, "preferred_twice_attempts": 0},
            3: {"step0": True, "preferred_twice": False, "preferred_twice_attempts": 0},
        }
        ai_rows = [
            {"task_id": 1, "created_at": "2026-09-15T11:00:00"},
            {"task_id": 1, "created_at": "2026-09-22T11:00:00"},
            {"task_id": 3, "created_at": "2026-09-22T12:00:00"},
        ]
        events = [
            {
                "task_id": 1, "channel": "signal", "kind": "misassign",
                "created_at": "2026-09-22T13:00:00", "detail": {"kind": "misassign"},
            },
        ]
        weeks = build_funnel_weekly(tickets, ai_rows, events, flags, keep=8)
        by_start = {w["week_start"]: w["funnel"] for w in weeks}
        born = by_start["2026-09-14"]
        dispatched = by_start["2026-09-21"]
        assert born["ticket_funnel"]["created"] == 1
        assert born["ticket_funnel"]["union"] == 1
        assert born["attempt_funnel"]["after_never_ai_attempts"] == 1
        assert born["attempt_funnel"]["misassign_events"] == 0
        assert dispatched["ticket_funnel"]["created"] == 1
        assert dispatched["attempt_funnel"]["never_ai_attempts"] == 1
        assert dispatched["attempt_funnel"]["after_never_ai_attempts"] == 2
        assert dispatched["attempt_funnel"]["step0_attempts"] == 1
        assert dispatched["attempt_funnel"]["denominator"] == 1
        assert dispatched["attempt_funnel"]["created_attempts"] == 3
        assert dispatched["attempt_funnel"]["misassign_events"] == 1


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
