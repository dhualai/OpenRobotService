"""归档审核人派审：纯逻辑单测（不连库、不调 LLM）。

运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_archive_review.py -v
"""

from __future__ import annotations

import random

import pytest

from ai.agents.AiDiagnosisPlatform.assigner.archive_review import (
    ArchiveReviewError,
    assign_archive_reviewer,
)
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.config import (
    parse_archive_reviewers,
)
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.domain import infer_domain
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.load import (
    compute_probabilities,
    pick_by_load_probability,
)


CFG = {
    "domains": {
        "调度系统": ["rev_a", "rev_b"],
        "车体硬件": ["rev_hw"],
        "车端软件": ["rev_sw", "handler"],
        "摇人吧": ["rev_yrb"],
    },
    "on_empty": "fail",
}


class TestInferDomain:
    def test_product_maps_to_domain(self):
        assert infer_domain(product="调度USP", allowed_domains=CFG["domains"]) == "调度系统"

    def test_project_marker_yaorenba(self):
        assert (
            infer_domain(project_name="摇人吧服务号-生产", allowed_domains=CFG["domains"])
            == "摇人吧"
        )

    def test_keyword_hardware(self):
        assert (
            infer_domain(title="货叉不动，轮子卡死", allowed_domains=CFG["domains"])
            == "车体硬件"
        )

    def test_keyword_elevator_peripheral_is_dispatch(self):
        assert (
            infer_domain(
                title="电梯外设任务误报完成致进车信号风险",
                description="外设平台外设管理 PLC 楼层信号",
                allowed_domains=CFG["domains"],
            )
            == "调度系统"
        )

    def test_unknown_returns_none(self):
        assert infer_domain(title="随便聊聊", allowed_domains=CFG["domains"]) is None


class TestLoadProbability:
    def test_weights_inverse_to_load(self):
        # load 0 → 1/(0+1)=1；load 1 → 0.5；合计 1.5 → 2/3 与 1/3
        probs = compute_probabilities(["a", "b"], {"a": 0, "b": 1})
        assert probs["a"] == pytest.approx(2 / 3)
        assert probs["b"] == pytest.approx(1 / 3)

    def test_equal_load_equal_prob(self):
        probs = compute_probabilities(["a", "b"], {"a": 2, "b": 2})
        assert probs["a"] == pytest.approx(0.5)
        assert probs["b"] == pytest.approx(0.5)

    def test_single_candidate_always_picked(self):
        assert pick_by_load_probability(["only"], {"only": 99}) == "only"

    def test_seeded_pick_is_reproducible(self):
        loads = {"a": 0, "b": 3}
        first = pick_by_load_probability(["a", "b"], loads, rng=random.Random(42))
        second = pick_by_load_probability(["a", "b"], loads, rng=random.Random(42))
        assert first == second

    def test_lower_load_more_often_but_not_always(self):
        loads = {"a": 0, "b": 3}
        # weight a=1, b=0.25 → P(a)=0.8, P(b)=0.2
        rng = random.Random(7)
        picks = [
            pick_by_load_probability(["a", "b"], loads, rng=rng)
            for _ in range(400)
        ]
        count_a = picks.count("a")
        count_b = picks.count("b")
        assert count_a > count_b
        assert count_b > 0  # 高负荷仍有机会被抽中


class TestArchiveReviewersOverrides:
    def test_normalize_and_effective_roundtrip(self, tmp_path, monkeypatch):
        from ai.agents.AiDiagnosisPlatform.assigner.archive_review import store as store_mod

        override = tmp_path / "runtime_overrides.yaml"
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("archive_reviewers: {}\n", encoding="utf-8")
        monkeypatch.setattr(store_mod, "_OVERRIDE_FILE", override)
        monkeypatch.setattr(store_mod, "_CONFIG_YAML", config_yaml)
        store_mod.invalidate_archive_reviewers_cache()

        saved = store_mod.save_archive_reviewers_overrides(
            domains={"调度系统": ["u1", "u1", "u2"], "车体硬件": []},
        )
        assert saved["domains"]["调度系统"] == ["u1", "u2"]
        assert "摇人吧" in saved["domains"]

        eff = store_mod.effective_archive_reviewers()
        assert eff["source"] == "runtime_overrides"
        assert eff["domains"]["调度系统"] == ["u1", "u2"]

        # mtime 未变时命中缓存（同一对象）
        again = store_mod.effective_archive_reviewers()
        assert again is eff

    def test_unknown_domain_rejected(self):
        from ai.agents.AiDiagnosisPlatform.assigner.archive_review.store import (
            normalize_archive_reviewers,
        )

        with pytest.raises(ValueError, match="未知问题领域"):
            normalize_archive_reviewers({"不存在的领域": ["u1"]})


class TestAssignArchiveReviewer:
    def test_ok_weighted_pick_with_seed(self):
        result = assign_archive_reviewer(
            title="调度任务阻塞无法下发",
            operator_id="handler",
            load_counts={"rev_a": 2, "rev_b": 0},
            config=CFG,
            rng=random.Random(1),
        )
        assert result.domain == "调度系统"
        assert result.reviewer_id in ("rev_a", "rev_b")
        assert result.load_snapshot == {"rev_a": 2, "rev_b": 0}
        assert result.probability_snapshot["rev_b"] > result.probability_snapshot["rev_a"]

    def test_excludes_operator(self):
        result = assign_archive_reviewer(
            title="定位漂移雷达无数据",
            operator_id="handler",
            load_counts={"rev_sw": 5, "handler": 0},
            config=CFG,
        )
        assert result.domain == "车端软件"
        assert result.reviewer_id == "rev_sw"
        assert "handler" not in result.candidate_ids

    def test_excludes_ticket_related_ids(self):
        result = assign_archive_reviewer(
            title="调度任务阻塞无法下发",
            operator_id="submitter",
            exclude_ids=["rev_a", "assignee"],
            load_counts={"rev_a": 0, "rev_b": 1},
            config=CFG,
            domain_hint="调度系统",
        )
        assert result.reviewer_id == "rev_b"
        assert "rev_a" not in result.candidate_ids

    def test_domain_unknown(self):
        with pytest.raises(ArchiveReviewError) as ei:
            assign_archive_reviewer(
                title="随便聊聊",
                operator_id="handler",
                config=CFG,
            )
        assert ei.value.code == "domain_unknown"

    def test_no_reviewers_configured(self):
        cfg = parse_archive_reviewers({"domains": {"调度系统": []}, "on_empty": "fail"})
        with pytest.raises(ArchiveReviewError) as ei:
            assign_archive_reviewer(
                title="调度任务阻塞",
                operator_id="handler",
                config=cfg,
                domain_hint="调度系统",
            )
        assert ei.value.code == "no_reviewers"

    def test_single_reviewer_skips_desensitize(self):
        """领域名单只有一人：跳过脱敏，直接派。"""
        cfg = {"domains": {"调度系统": ["handler"]}, "on_empty": "fail"}
        result = assign_archive_reviewer(
            title="调度任务阻塞",
            operator_id="handler",
            config=cfg,
            domain_hint="调度系统",
        )
        assert result.reviewer_id == "handler"
        assert "跳过脱敏" in (result.reason or "")

    def test_multi_all_excluded_falls_back(self):
        """多人名单全被脱敏时回落，仍可派给相关人。"""
        cfg = {"domains": {"调度系统": ["rev_a", "rev_b"]}, "on_empty": "fail"}
        result = assign_archive_reviewer(
            title="调度任务阻塞",
            operator_id="rev_a",
            exclude_ids=["rev_b"],
            config=cfg,
            domain_hint="调度系统",
            rng=random.Random(0),
        )
        assert result.reviewer_id in ("rev_a", "rev_b")
        assert "回落原名单" in (result.reason or "")

    def test_domain_hint_skips_infer(self):
        result = assign_archive_reviewer(
            title="无关正文",
            operator_id="x",
            config=CFG,
            domain_hint="车体硬件",
        )
        assert result.reviewer_id == "rev_hw"
        assert result.domain == "车体硬件"
