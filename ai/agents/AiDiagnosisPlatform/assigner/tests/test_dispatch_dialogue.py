"""一次派单共用多轮对话：后一轮调用带上前面各轮原文。

不连库。模型用假客户端。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_dispatch_dialogue.py -v
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dialogue import DispatchDialogue
from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _eng() -> EngineerProfile:
    return EngineerProfile(
        id="u-a",
        name="甲",
        department="智能规划研究院",
        job_level=1,
        responsibility_modules={"摇人吧服务号": {"前端": ["页面"]}},
        duty_text="负责前端",
    )


def _ticket() -> TicketContext:
    return TicketContext(
        id="t-dialogue",
        title="现场报障",
        problem_description="车子停了",
        status="new",
    )


class TestDispatchDialogue:
    """后一轮 history 带上前一轮的问题和回答。"""

    def test_second_round_sees_first_prompt_and_reply(self):
        """正常流程：第二轮用 chat 带上第一轮问题和模型原文。"""
        dialogue = DispatchDialogue()
        seen_complete = []
        seen_chat = []

        async def complete(prompt, **kwargs):
            seen_complete.append(prompt)
            return "部门是智能规划研究院"

        async def chat(messages, **kwargs):
            seen_chat.append(list(messages))
            return "派给甲"

        llm = SimpleNamespace(complete=complete, chat=chat)

        async def _go():
            await dialogue.complete(llm, "第一轮问题")
            await dialogue.complete(llm, "第二轮问题")

        asyncio.run(_go())
        assert seen_complete == ["第一轮问题"]
        assert [m["role"] for m in seen_chat[0]] == ["user", "assistant", "user"]
        assert seen_chat[0][0]["content"] == "第一轮问题"
        assert seen_chat[0][1]["content"] == "部门是智能规划研究院"
        assert seen_chat[0][2]["content"] == "第二轮问题"

    def test_failed_call_does_not_keep_a_dangling_user_turn(self):
        """异常流程：模型调用失败时，这一轮不写入对话。"""
        dialogue = DispatchDialogue()

        async def complete(prompt, **kwargs):
            raise RuntimeError("上游超时")

        llm = SimpleNamespace(complete=complete, chat=AsyncMock())

        async def _go():
            await dialogue.complete(llm, "这一轮失败")

        try:
            asyncio.run(_go())
        except RuntimeError:
            pass
        assert dialogue.messages == []

    def test_note_is_carried_into_the_next_round(self):
        """正常流程：没有调用模型的步骤结论，下一轮能读到。"""
        dialogue = DispatchDialogue()
        seen = []

        async def chat(messages, **kwargs):
            seen.append(list(messages))
            return "已收到"

        llm = SimpleNamespace(complete=AsyncMock(), chat=chat)
        dialogue.note("Step4 精排", "#1 甲 总分=0.90")

        async def _go():
            await dialogue.complete(llm, "请做最终选择")

        asyncio.run(_go())
        llm.complete.assert_not_called()
        assert seen[0][0]["role"] == "user"
        assert "Step4 精排" in seen[0][0]["content"]
        assert "甲" in seen[0][0]["content"]
        assert seen[0][-1] == {"role": "user", "content": "请做最终选择"}


class TestStep6UsesDialogue:
    """Step6 最终决策沿用同一条对话。"""

    def test_adecide_sends_prior_rounds(self):
        """正常流程：Step6 调用时，history 里已经有前面步骤的模型原文。"""
        dialogue = DispatchDialogue()
        dialogue.add("user", "请判断部门")
        dialogue.add("assistant", "部门是智能规划研究院")
        captured = {}

        async def chat(messages, **kwargs):
            captured["messages"] = list(messages)
            return (
                '{"can_decide":true,"engineer_id":"u-a","engineer_name":"甲",'
                '"confidence_score":0.9,"reasoning":"甲最合适","decision_type":"auto"}'
            )

        llm = SimpleNamespace(complete=AsyncMock(), chat=chat)
        ranked = {
            "u-a": {
                "total_score": 0.9, "llm_score": 0.9,
                "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
        }

        async def _go():
            with patch("ai.core.get_llm_client", AsyncMock(return_value=llm)):
                return await LlmDecision(SimpleNamespace(
                    job_level_penalty={1: 1.0},
                    preferred_floor=0.9,
                    llm_decision_topk=0,
                    yaorenba_force_module_owner=False,
                    module_keywords={},
                    module_anchor_texts={},
                )).adecide(
                    _ticket(), [_eng()], RecallResult(), ranked, dialogue=dialogue,
                )

        result = asyncio.run(_go())
        assert result is not None
        assert result.engineer_id == "u-a"
        llm.complete.assert_not_called()
        assert "【公共铁律】" in captured["messages"][-1]["content"]
        assert captured["messages"][1]["content"] == "部门是智能规划研究院"
        assert dialogue.messages[-1]["role"] == "assistant"
        assert "甲最合适" in dialogue.messages[-1]["content"]
