"""Self-tests for the veto-rule metric (fake judge)."""

import pytest

from automation.src.ai_metrics import check_veto_rules, build_veto_prompt


class FakeJudge:
    def __init__(self, reply: str):
        self._reply = reply
        self.last_user_prompt = ""

    async def complete(self, system_prompt, user_prompt):
        self.last_user_prompt = user_prompt
        return self._reply


RULES = ["不得编造具体故障结论", "不得跳过排查步骤", "不得追问排查问题"]


class TestVetoRules:
    @pytest.mark.asyncio
    async def test_no_violation(self):
        judge = FakeJudge('{"violated": [], "reason": "全部符合"}')
        result = await check_veto_rules("q", "a", RULES, judge)
        assert result["vetoed"] is False
        assert result["violated"] == []
        assert result["uncertain"] is False

    @pytest.mark.asyncio
    async def test_single_violation(self):
        judge = FakeJudge('{"violated": [1], "reason": "编造了故障结论"}')
        result = await check_veto_rules("q", "a", RULES, judge)
        assert result["vetoed"] is True
        assert result["violated"] == [1]

    @pytest.mark.asyncio
    async def test_multiple_violations(self):
        judge = FakeJudge('{"violated": [1, 3], "reason": "编造且追问"}')
        result = await check_veto_rules("q", "a", RULES, judge)
        assert result["vetoed"] is True
        assert result["violated"] == [1, 3]

    @pytest.mark.asyncio
    async def test_unparsable_is_uncertain_veto(self):
        judge = FakeJudge("I think it's mostly fine")
        result = await check_veto_rules("q", "a", RULES, judge)
        assert result["vetoed"] is True
        assert result["uncertain"] is True
        assert "unparsable" in result["reason"]

    @pytest.mark.asyncio
    async def test_bad_violated_type_is_uncertain(self):
        judge = FakeJudge('{"violated": "yes", "reason": "x"}')
        result = await check_veto_rules("q", "a", RULES, judge)
        assert result["vetoed"] is True
        assert result["uncertain"] is True

    @pytest.mark.asyncio
    async def test_empty_rules_skips_judge(self):
        judge = FakeJudge("not called")
        result = await check_veto_rules("q", "a", [], judge)
        assert result["vetoed"] is False
        assert "skipped" in result["reason"]

    @pytest.mark.asyncio
    async def test_fenced_json(self):
        judge = FakeJudge('```json\n{"violated": [2], "reason": "跳过排查"}\n```')
        result = await check_veto_rules("q", "a", RULES, judge)
        assert result["vetoed"] is True
        assert result["violated"] == [2]

    @pytest.mark.asyncio
    async def test_prompt_contains_rules_and_answer(self):
        judge = FakeJudge('{"violated": [], "reason": "ok"}')
        await check_veto_rules("问题Q", "回答A", ["规则R"], judge)
        assert "规则R" in judge.last_user_prompt
        assert "问题Q" in judge.last_user_prompt
        assert "回答A" in judge.last_user_prompt

    @pytest.mark.asyncio
    async def test_veto_independent_of_rubric_score(self):
        judge = FakeJudge('{"violated": [1], "reason": "高分但编造"}')
        result = await check_veto_rules("q", "a", RULES, judge)
        assert result["vetoed"] is True  # 高分不可覆盖否决
