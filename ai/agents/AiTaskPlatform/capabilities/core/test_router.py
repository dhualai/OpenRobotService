"""Router 的 challenge 字段：识别「用户在质疑此前的结论」。

为什么要它：原来这件事靠一张**硬编码关键词表**（「重新分析/分析是错的/不对吧」…），
用户换个说法（「你这结论站不住」）就失效。现在折进**本来就要做的那次意图分类**里，
不多花一次 LLM 调用；关键词表降级为兜底（分类失败时仍要能接住，见 discuss_flow）。

不调真 LLM：假客户端按需吐 JSON。
"""
import asyncio
import json

from ai.agents.AiTaskPlatform.capabilities.core.router import (
    CONFIDENCE_THRESHOLD,
    Router,
)


class _LLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def complete(self, prompt, system_prompt=None, max_tokens=2000,
                       temperature=0.1, thinking=False):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _Boom:
    async def complete(self, *a, **k):
        raise AssertionError("这一步不该调 LLM")


def _full(reply):
    return asyncio.run(Router.classify_full(_LLM(reply), "你这结论站不住吧"))


def test_challenge_true_is_reported():
    out = _full(json.dumps({"intent": "general", "confidence": 0.9, "challenge": True}))
    assert out["intent"] == "general"
    assert out["challenge"] is True


def test_challenge_false_when_classifier_says_so():
    out = _full(json.dumps({"intent": "general", "confidence": 0.9, "challenge": False}))
    assert out["challenge"] is False


def test_missing_challenge_defaults_to_false():
    """分类器没给这个字段 → 保守当「不是质疑」，不要误触发强制重跑日志。"""
    out = _full(json.dumps({"intent": "log_analysis", "confidence": 0.9}))
    assert out["challenge"] is False


def test_prompt_asks_for_challenge():
    """prompt 里必须真的问了，否则模型不会给（约束只在注释里等于没写）。"""
    llm = _LLM(json.dumps({"intent": "general", "confidence": 0.9}))
    asyncio.run(Router.classify_full(llm, "随便问一句"))
    assert "challenge" in llm.prompts[0]


def test_low_confidence_returns_none_not_a_guess():
    """低置信 → None，调用方据此回退（回退到关键词，不是回退到「不是质疑」）。"""
    out = _full(json.dumps({
        "intent": "general",
        "confidence": CONFIDENCE_THRESHOLD - 0.1,
        "challenge": True,
    }))
    assert out is None


def test_parse_failure_returns_none():
    assert _full("完全不是 JSON") is None
    assert _full(json.dumps({"intent": "不存在的意图", "confidence": 0.99})) is None


def test_llm_error_returns_none():
    assert _full(RuntimeError("上游炸了")) is None


def test_classify_still_returns_plain_intent():
    """老入口保持兼容：只要 intent，拿不到就给 fallback。"""
    llm = _LLM(json.dumps({"intent": "history", "confidence": 0.9, "challenge": True}))
    assert asyncio.run(Router.classify(llm, "以前有没有遇到过")) == "history"
    assert asyncio.run(Router.classify(_LLM("坏输出"), "x", fallback="general")) == "general"
