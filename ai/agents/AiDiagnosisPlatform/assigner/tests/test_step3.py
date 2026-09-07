"""Step3 L1：给大模型看 姓名+ID，并带回一句话原因给 Step6。

不调 LLM、不连库。转派旁路本版恒空。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_step3.py -v
"""

from types import SimpleNamespace

from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
from ai.agents.AiDiagnosisPlatform.assigner.recall.history_recall import HistoryRecall
from ai.agents.AiDiagnosisPlatform.assigner.recall.llm_recall import LlmRecall
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cfg(**kwargs):
    data = dict(
        ranker_weights={"llm_match": 0.70, "semantic_match": 0.15, "history_match": 0.15},
        job_level_penalty={1: 1.0, 2: 0.90, 3: 0.90, 99: 0.90},
        contact_bonus=1.0,
        preferred_floor=0.9,
        llm_decision_topk=0,
        history_recall={},
        llm_recall={
            "single_top_k": 5, "batch_top_k": 3, "single_round_max": 12, "batch_size": 8,
        },
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


def _eng(eid: str, name: str) -> EngineerProfile:
    return EngineerProfile(
        id=eid,
        name=name,
        department="智能规划研究院",
        job_level=1,
        responsibility_modules={"摇人吧服务号": {"前端": ["页面"]}},
        duty_text="负责前端",
    )


def _ticket(**kwargs) -> TicketContext:
    data = {
        "id": "t-step3",
        "title": "现场报障",
        "problem_description": "车子停了",
        "status": "new",
    }
    data.update(kwargs)
    return TicketContext(**data)


class TestDispatchTicketText:
    """A 路向量文本：固定四栏，缺栏写无。"""

    def test_all_slots_present_when_empty(self):
        """正常流程：四栏都在，空值写成无。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.dispatch_text import (
            build_dispatch_ticket_text,
        )
        text = build_dispatch_ticket_text(title="页面打不开", description="点了没反应")
        assert "标题：页面打不开" in text
        assert "描述：点了没反应" in text
        assert "车型：无" in text
        assert "故障码：无" in text

    def test_description_truncated(self):
        """边界条件：描述超过 300 字截断。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.dispatch_text import (
            build_dispatch_ticket_text,
        )
        text = build_dispatch_ticket_text(title="t", description="长" * 400)
        desc_line = [ln for ln in text.splitlines() if ln.startswith("描述：")][0]
        assert len(desc_line) == len("描述：") + 300

    def test_query_uses_same_template(self):
        """正常流程：检索拼法和入库模板一致。"""
        rec = HistoryRecall(_cfg())
        q = rec._build_query_text(_ticket(
            title="定位漂移",
            problem_description="无法重定位",
            robot_type="S20",
            fault_code="E1001",
        ))
        assert q == (
            "标题：定位漂移\n"
            "描述：无法重定位\n"
            "车型：S20\n"
            "故障码：E1001"
        )


class TestL1PromptHasName:
    """Step3 L1 喂给大模型的候选人必须有姓名和 ID。"""

    def test_prompt_lists_name_and_id(self):
        """正常流程：L1 候选行含 姓名: 与 ID:。"""
        prompt = LlmRecall(_cfg())._build_prompt(_ticket(), [_eng("u-a", "甲")], top_k=1)
        assert "姓名:甲" in prompt
        assert "ID:u-a" in prompt
        assert "责任模块:" in prompt
        assert "职责:负责前端" in prompt
        assert "engineer_name" in prompt
        assert "reason 必填" in prompt
        assert "这类故障" in prompt
        assert "产品经理" in prompt
        assert "【反幻觉】" in prompt
        assert "禁止编造" in prompt
        assert "字面等于" in prompt
        assert "卡片对不上" not in prompt
        assert "不要用过往工单经验" in prompt
        assert "不影响打分" not in prompt
        assert "候选ID: u-a" not in prompt
        from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import ticket_fields_block
        assert ticket_fields_block(_ticket()).rstrip() in prompt


class TestL1ParseReason:
    """L1 JSON 解析分数 + 原因；缺 reason 不阻断打分。"""

    def test_parse_score_reason_and_name(self):
        """正常流程：同时填 id / 姓名 / reason → 分数和原因都在。"""
        scores, reasons = LlmRecall(_cfg())._parse(
            '{"rankings":[{"engineer_id":"u-a","engineer_name":"甲","confidence":0.9,"reason":"甲负责该模块"}]}',
            [_eng("u-a", "甲")],
        )
        assert scores == {"u-a": 0.9}
        assert reasons == {"u-a": "甲负责该模块"}

    def test_parse_without_reason_still_scores(self):
        """异常流程：没有 reason 字段 → 仍打分，原因为空串。"""
        scores, reasons = LlmRecall(_cfg())._parse(
            '{"rankings":[{"engineer_id":"u-a","confidence":0.8}]}',
            [_eng("u-a", "甲")],
        )
        assert scores == {"u-a": 0.8}
        assert reasons == {"u-a": ""}

    def test_parse_accepts_full_label(self):
        """正常流程：engineer_id 整段复制「姓名:甲 ID:u-a」也能对上。"""
        scores, reasons = LlmRecall(_cfg())._parse(
            '{"rankings":[{"engineer_id":"姓名:甲 ID:u-a","engineer_name":"甲","confidence":0.7,"reason":"对口"}]}',
            [_eng("u-a", "甲")],
        )
        assert scores["u-a"] == 0.7
        assert reasons["u-a"] == "对口"


class TestL1ReasonGoesToStep6:
    """L1 原因带姓名进 Step6 仲裁 prompt。"""

    def test_step6_prompt_shows_l1_reason_with_name(self):
        """正常流程：仲裁候选下能看到 L1原因，且该人有 姓名:。"""
        recall = RecallResult()
        recall.llm_reasons = {"u-a": "甲负责前端页面"}
        ranked = {
            "u-a": {
                "is_creator": False, "total_score": 0.9,
                "llm_score": 0.9, "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
        }
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(), [_eng("u-a", "甲")], recall, ranked,
        )
        assert "姓名:甲 ID:u-a" in prompt
        assert "L1原因: 甲负责前端页面" in prompt


class TestTransferSignalsEmpty:
    """L3 转派旁路本版恒空。"""

    def test_empty_interface(self):
        """正常流程：empty_transfer_signals 只有空 boosts/penalties。"""
        sig = HistoryRecall(_cfg()).empty_transfer_signals()
        assert sig == {"boosts": {}, "penalties": {}}
        recall = RecallResult()
        assert recall.transfer_signals == {"boosts": {}, "penalties": {}}
        assert recall.llm_reasons == {}


class TestL1TopKCap:
    """单轮 Top5；分批每批 Top3，合并后全进 Step4，不再决选截断。"""

    def test_clip_keeps_highest_k(self):
        """正常流程：单轮 7 人打分 → 只留分最高的 5 个。"""
        scores = {f"u-{i}": 0.1 * i for i in range(1, 8)}
        reasons = {f"u-{i}": f"r{i}" for i in range(1, 8)}
        out_s, out_r = LlmRecall(_cfg())._clip_top(scores, reasons, 5)
        assert list(out_s) == ["u-7", "u-6", "u-5", "u-4", "u-3"]
        assert set(out_r) == set(out_s)
        assert out_r["u-7"] == "r7"

    def test_clip_fewer_than_k_keeps_all(self):
        """边界：人不足 K → 全留。"""
        out_s, out_r = LlmRecall(_cfg())._clip_top({"u-a": 0.9}, {"u-a": "甲"}, 5)
        assert out_s == {"u-a": 0.9}
        assert out_r == {"u-a": "甲"}

    def test_single_round_prompt_asks_top_k_not_all(self):
        """正常流程：8 人单轮 → prompt 要 Top5，不是评估全部。"""
        engs = [_eng(f"u-{i}", f"人{i}") for i in range(8)]
        rec = LlmRecall(_cfg())
        k = min(rec._single_top_k, len(engs))
        prompt = rec._build_prompt(_ticket(), engs, top_k=k)
        assert "Top 5" in prompt
        assert "全部 8 位" not in prompt

    def test_batch_union_keeps_all_three_batches(self):
        """正常流程：三批各 Top3 → 9 人全部保留，不截到 5。"""
        stage1 = {f"b{b}-{i}": 0.9 - 0.01 * (b * 3 + i) for b in range(3) for i in range(3)}
        reasons = {k: "r" for k in stage1}
        out_s, out_r = LlmRecall._keep_batch_union(stage1, reasons)
        assert len(out_s) == 9
        assert set(out_s) == set(stage1)
        assert set(out_r) == set(stage1)

    def test_reads_config_values(self):
        """正常流程：single_top_k / batch_top_k 从配置读。"""
        rec = LlmRecall(_cfg(llm_recall={
            "single_top_k": 4, "batch_top_k": 2, "single_round_max": 10, "batch_size": 6,
        }))
        assert rec._single_top_k == 4
        assert rec._batch_top_k == 2
        assert rec._single_round_max == 10
        assert rec._batch_size == 6

    def test_final_top_k_alias(self):
        """兼容：只有旧键 final_top_k 时当作单轮人数。"""
        rec = LlmRecall(_cfg(llm_recall={
            "final_top_k": 4, "batch_top_k": 3, "single_round_max": 12, "batch_size": 8,
        }))
        assert rec._single_top_k == 4


class TestL3AutoCluster:
    """B 路：已解决/已关闭单自动聚簇，不手切问题域。"""

    def test_two_groups_form_two_clusters(self):
        """正常流程：两团明显分开的向量 → 两簇，小噪声团丢掉。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            cluster_by_similarity,
        )
        import numpy as np
        a = np.array([[1.0, 0.0], [0.99, 0.01], [0.98, 0.02], [0.97, 0.03]])
        b = np.array([[0.0, 1.0], [0.01, 0.99], [0.02, 0.98], [0.03, 0.97]])
        noise = np.array([[0.7, 0.7]])
        embs = np.vstack([a, b, noise])
        groups = cluster_by_similarity(embs, merge_threshold=0.95, min_size=3)
        sizes = sorted(len(g) for g in groups)
        assert sizes == [4, 4]

    def test_chain_does_not_stay_one_cluster(self):
        """异常流程：A≈B、B≈C 但 A 远 C → 不能靠串门并成一簇。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            cluster_by_similarity,
        )
        import numpy as np
        a = np.array([1.0, 0.0, 0.0])
        ab = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
        b = np.array([0.0, 1.0, 0.0])
        bc = np.array([0.0, 1.0, 1.0]) / np.sqrt(2)
        c = np.array([0.0, 0.0, 1.0])
        embs = np.vstack([a, ab, b, bc, c])
        groups = cluster_by_similarity(embs, merge_threshold=0.55, min_size=3)
        assert all(len(g) < 5 for g in groups)

    def test_query_lands_in_near_cluster(self):
        """正常流程：新单靠近 A 团 → 只落入 A。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            cluster_by_similarity,
            cluster_centroids,
            pick_cluster_ids,
        )
        import numpy as np
        a = np.array([[1.0, 0.0], [0.99, 0.01], [0.98, 0.02], [0.97, 0.03]])
        b = np.array([[0.0, 1.0], [0.01, 0.99], [0.02, 0.98], [0.03, 0.97]])
        embs = np.vstack([a, b])
        groups = cluster_by_similarity(embs, merge_threshold=0.95, min_size=3)
        cents = cluster_centroids(embs, groups)
        hits = pick_cluster_ids(np.array([1.0, 0.0]), cents, assign_threshold=0.7, top_k=2)
        assert hits
        assert hits[0][1] > 0.9

    def test_far_query_matches_nothing(self):
        """异常流程：新单离所有簇都远 → 空，不通配。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            pick_cluster_ids,
        )
        import numpy as np
        cents = np.array([[1.0, 0.0], [0.0, 1.0]])
        hits = pick_cluster_ids(np.array([0.5, 0.5]), cents, assign_threshold=0.99, top_k=2)
        assert hits == []

    def test_time_decay_floors_at_point_four(self):
        """正常流程：刚结=1.0，无限久逼近 0.4，不会掉到 0。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.history_recall import (
            time_decay_factor,
        )
        assert time_decay_factor(0) == 1.0
        assert 0.60 < time_decay_factor(90) < 0.65
        assert 0.47 < time_decay_factor(180) < 0.50
        assert abs(time_decay_factor(10_000) - 0.4) < 0.01
        assert time_decay_factor(10_000) >= 0.4

    def test_similar_person_score_max_and_clip(self):
        """正常流程：相似人分取最高张，一张不打折，超过 1 截断。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.history_recall import (
            similar_person_score,
        )
        assert similar_person_score([0.72]) == 0.72
        assert similar_person_score([0.40, 0.85, 0.50]) == 0.85
        assert similar_person_score([1.25, 0.90]) == 1.0

    def test_cluster_person_score_is_absolute(self):
        """正常流程：问题域人分不按本批拉满；弱命中低于 0.5，强命中可到 1。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            cluster_person_score,
        )
        weak = cluster_person_score(count=1, freshness=0.4, cluster_sim=0.40)
        mid = cluster_person_score(count=1, freshness=1.0, cluster_sim=0.70)
        strong = cluster_person_score(count=10, freshness=1.0, cluster_sim=0.95)
        assert 0.10 < weak < 0.25
        assert 0.50 < mid < 0.75
        assert strong == 1.0
        assert cluster_person_score(count=20, freshness=1.0, cluster_sim=1.0) == 1.0

    def test_project_to_2d_keeps_two_blobs_apart(self):
        """正常流程：两团高维向量投到二维后仍分开。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            project_to_2d,
        )
        import numpy as np
        a = np.array([[1.0, 0.0, 0.0], [0.99, 0.01, 0.0], [0.98, 0.02, 0.0]])
        b = np.array([[0.0, 1.0, 0.0], [0.01, 0.99, 0.0], [0.02, 0.98, 0.0]])
        xy = project_to_2d(np.vstack([a, b]))
        assert xy.shape == (6, 2)
        mid_a = xy[:3].mean(axis=0)
        mid_b = xy[3:].mean(axis=0)
        assert float(np.linalg.norm(mid_a - mid_b)) > 0.8

    def test_ticket_points_mark_noise(self):
        """正常流程：未入簇的点 cluster_id=-1。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            build_ticket_points,
        )
        import numpy as np
        recs = [
            {"ticket_id": "1", "title": "A", "engineer_id": "u-a"},
            {"ticket_id": "2", "title": "B", "engineer_id": "u-b"},
        ]
        xy = np.array([[0.1, 0.2], [0.8, -0.3]])
        pts = build_ticket_points(recs, [[0]], xy)
        assert pts[0]["cluster_id"] == 0
        assert pts[1]["cluster_id"] == -1
        assert pts[0]["x"] == 0.1

    def test_cluster_snapshot_from_cache(self):
        """正常流程：缓存里的簇能读出工单和结单人，未入簇算噪声。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall import expertise_recall as er
        er._cache.update({
            "hash": "x",
            "centroids": object(),
            "cluster_people": [{"u-a": {"count": 3, "last_ts": 1.0}}],
            "cluster_titles": [["车子停了"]],
            "cluster_tickets": [[{
                "ticket_id": "1", "title": "车子停了", "engineer_id": "u-a",
            }]],
            "ticket_total": 5,
            "ticket_points": [{
                "ticket_id": "1", "title": "车子停了", "engineer_id": "u-a",
                "cluster_id": 0, "x": 0.2, "y": -0.1,
            }],
        })
        snap = er.cluster_snapshot_from_cache({"u-a": "甲"})
        assert snap["ready"] is True
        assert snap["clustered"] == 1
        assert snap["noise"] == 4
        assert snap["clusters"][0]["people"][0]["name"] == "甲"
        assert snap["clusters"][0]["tickets"][0]["engineer_name"] == "甲"
        assert snap["points"][0]["engineer_name"] == "甲"
        assert snap["points"][0]["x"] == 0.2
        er.invalidate_expertise_cache()
        assert er.cluster_snapshot_from_cache()["ready"] is False

    def test_count_term_softer_than_raw_log(self):
        """正常流程：次数收益弱于纯 ln(1+n)，结 10 张不会碾压结 1 张。"""
        from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
            count_term,
        )
        import numpy as np
        soft = count_term(10) / count_term(1)
        raw = float(np.log1p(10) / np.log1p(1))
        assert count_term(10) > count_term(1)
        assert soft < raw
        assert 1.2 < soft < 1.8
