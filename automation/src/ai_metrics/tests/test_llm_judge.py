"""Self-tests for faithfulness and rubric judge metrics (fake judge)."""

import pytest

from automation.src.ai_metrics import judge_faithfulness, judge_rubric


class FakeJudge:
    def __init__(self, reply: str):
        self._reply = reply
        self.last_user_prompt = ""

    async def complete(self, system_prompt, user_prompt):
        self.last_user_prompt = user_prompt
        return self._reply


class TestJudgeFaithfulness:
    @pytest.mark.asyncio
    async def test_grounded_answer(self):
        judge = FakeJudge('{"score": 1.0, "reason": "完全基于文档"}')
        result = await judge_faithfulness(
            "车子不动了怎么办",
            "请确认定位状态",
            ["定位状态丢失会导致车辆无法移动"],
            judge,
        )
        assert result["score"] == 1.0

    @pytest.mark.asyncio
    async def test_hallucinated_answer(self):
        judge = FakeJudge('{"score": 0.1, "reason": "编造了检查项"}')
        result = await judge_faithfulness(
            "车子不动了怎么办",
            "请更换主控板并重刷固件",
            ["定位状态丢失会导致车辆无法移动"],
            judge,
        )
        assert result["score"] < 0.5

    @pytest.mark.asyncio
    async def test_fenced_json(self):
        judge = FakeJudge('```json\n{"score": 0.8, "reason": "ok"}\n```')
        result = await judge_faithfulness("q", "a", ["doc"], judge)
        assert result["score"] == 0.8

    @pytest.mark.asyncio
    async def test_unparsable_returns_zero(self):
        judge = FakeJudge("I think it's fine")
        result = await judge_faithfulness("q", "a", ["doc"], judge)
        assert result["score"] == 0.0
        assert "unparsable" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_docs_skips(self):
        judge = FakeJudge("anything")
        result = await judge_faithfulness("q", "a", [], judge)
        assert result["score"] == 1.0

    @pytest.mark.asyncio
    async def test_score_clamped(self):
        judge = FakeJudge('{"score": 9.0, "reason": "x"}')
        result = await judge_faithfulness("q", "a", ["doc"], judge)
        assert result["score"] == 1.0

    @pytest.mark.asyncio
    async def test_prompt_contains_docs(self):
        judge = FakeJudge('{"score": 1.0, "reason": "ok"}')
        await judge_faithfulness("问题Q", "回答A", ["文档D"], judge)
        assert "文档D" in judge.last_user_prompt
        assert "问题Q" in judge.last_user_prompt


class TestJudgeRubric:
    @pytest.mark.asyncio
    async def test_score_parsed(self):
        judge = FakeJudge('{"score": 4, "reason": "不错"}')
        result = await judge_rubric("q", "a", "回答要清晰", judge)
        assert result["score"] == 4.0
        assert result["reason"] == "不错"

    @pytest.mark.asyncio
    async def test_bad_output_zero(self):
        judge = FakeJudge("没有 JSON")
        result = await judge_rubric("q", "a", "r", judge)
        assert result["score"] == 0.0

    @pytest.mark.asyncio
    async def test_score_clamped(self):
        judge = FakeJudge('{"score": 7, "reason": "x"}')
        result = await judge_rubric("q", "a", "r", judge)
        assert result["score"] == 5.0

    @pytest.mark.asyncio
    async def test_negative_clamped(self):
        judge = FakeJudge('{"score": -2, "reason": "x"}')
        result = await judge_rubric("q", "a", "r", judge)
        assert result["score"] == 0.0
