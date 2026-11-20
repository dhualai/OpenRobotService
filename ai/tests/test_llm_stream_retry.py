"""LLM 流式 400/upstream_error 重试 + relay 模型降级 + relay 思考开关单测。

生产实锤（2026-08-21 11:20 日志）：中转商 upstream_error 带 HTTP 400，
旧的重试白名单只认 httpx 网络异常，400 直接穿出不重试。修复后：
1. 400（未产出 token，重试无重复输出风险）同样重试 3 次；
2. relay 主模型 3 次全失败后按 RELAY_FALLBACK_MODELS 降级换模型
   （如 gpt 挂了 → claude-opus-4-8 → claude-sonnet-5）；
3. 已向调用方产出内容后不再换模型重发（防重复输出）；
4. RELAY_THINKING 开关控制中转站 Claude 是否传 thinking 字段。
"""
import asyncio
import sys
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

from ai.core.llm import (  # noqa: E402
    LLMClient,
    LLMProvider,
    OpenAIProvider,
    DeepSeekProvider,
)
from ai.exceptions import AITimeoutError, ServiceUnavailableError  # noqa: E402


class _FakeResp:
    """双形态响应：int=错误状态码；list=SSE 行（流式，行尾可抛断流）；dict=非流式 JSON"""

    def __init__(self, script):
        self._script = script
        if isinstance(script, int):
            self.status_code = script
            self._body = {"error": {"message": "Upstream request failed"}}
        else:
            self.status_code = 200
            self._body = script if isinstance(script, dict) else None
        self.text = str(self._body or "")

    async def aread(self):
        return b'{"error":{"message":"Upstream request failed","type":"upstream_error"}}'

    async def aiter_lines(self):
        lines = self._script if isinstance(self._script, list) else ["data: [DONE]"]
        for item in lines:
            # ("sleep", sec)：asyncio.sleep（受测试基建短路）；("busy", sec)：
            # time.sleep 真实阻塞（表达上游 hang 的真实时间流逝，不受短路影响）
            if isinstance(item, tuple):
                kind, sec = item
                if kind == "sleep":
                    await asyncio.sleep(sec)
                elif kind == "busy":
                    time.sleep(sec)
                continue
            yield item
        if isinstance(self._script, list):
            raise httpx.RemoteProtocolError("peer closed connection mid-stream")

    def json(self):
        return self._body


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return False


class _FakeHttpClient:
    """按预设脚本依次返回响应，记录每次请求的 payload。
    脚本元素：int=状态码；list=SSE 行（流式）；dict=非流式 JSON 响应"""

    def __init__(self, scripts):
        self._scripts = scripts
        self.calls = 0
        self.payloads = []

    def _take(self):
        item = self._scripts[min(self.calls, len(self._scripts) - 1)]
        self.calls += 1
        if item == "timeout":
            # 连接超时形态（1015 事故：deepseek 官方崩即此形态）
            raise httpx.ConnectTimeout("simulated connect timeout")
        return _FakeResp(item)

    def stream(self, method, url, **kwargs):
        self.payloads.append(kwargs.get("json") or {})
        return _FakeStreamCtx(self._take())

    async def post(self, url, json=None, **kwargs):
        self.payloads.append(json or {})
        return self._take()


def _fake_impl():
    return SimpleNamespace(
        build_payload=lambda **kw: kw,
        is_responses_model=lambda m: False,
        get_api_url=lambda base: base,
        get_responses_url=lambda base: base,
    )


async def _make_llm(monkeypatch, scripts, *, provider=LLMProvider.DEEPSEEK,
                    model="test-model", relay_fallback="", relay_thinking=False,
                    impl=None, relay_scripts=None, relay_api_key="",
                    relay_model="gpt-5.6-sol", relay_base="http://relay-test"):
    """scripts=主站响应脚本；relay_scripts/relay_api_key=跨站热备（relay 站）。
    返回 (llm, main_fake, relay_fake)：按站点断言请求落在哪个站。"""
    llm = LLMClient.__new__(LLMClient)
    llm.provider = provider
    llm.model = model
    llm.api_key = "test-key"
    llm.base_url = "http://test"
    llm._thinking_default = None
    llm.reasoning_effort = "low"
    llm.config = SimpleNamespace(
        relay_fallback_models=relay_fallback, relay_thinking=relay_thinking,
        llm_stream_first_timeout=0.05,
        relay_api_key=relay_api_key, relay_model=relay_model,
        relay_base_url=relay_base, deepseek_api_key="test-key")
    llm._provider_impl = impl or _fake_impl()
    fake = _FakeHttpClient(scripts)
    llm._get_client = AsyncMock(return_value=fake)
    relay_fake = _FakeHttpClient(relay_scripts if relay_scripts is not None else [])
    llm._relay_client = relay_fake
    async def _client_for(p):
        return await llm._get_client() if p == llm.provider else relay_fake
    llm._client_for = _client_for
    async def _fast_sleep(_): return None
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    return llm, fake, relay_fake


# ---------- 基础重试（deepseek 直连，无降级） ----------

async def test_stream_400_retries_then_succeeds(monkeypatch):
    """前两次 400，第三次 200：重试救回，正常结束"""
    llm, fake, _relay = await _make_llm(monkeypatch, [400, 400, 200])
    tokens = []
    async for tok in llm.stream(prompt="测试", system_prompt="s"):
        tokens.append(tok)
    assert fake.calls == 3


async def test_stream_400_exhausted_raises(monkeypatch):
    """三次都 400：重试耗尽后抛 ServiceUnavailableError（单模型，不降级）"""
    llm, fake, _relay = await _make_llm(monkeypatch, [400, 400, 400])
    with pytest.raises(ServiceUnavailableError):
        async for _ in llm.stream(prompt="测试"):
            pass
    assert fake.calls == 3


# ---------- relay 模型降级 ----------

async def test_stream_relay_fallback_to_claude(monkeypatch):
    """relay 主模型(gpt) 3 次 400 全挂 → 降级 claude 成功出 token"""
    llm, fake, _relay = await _make_llm(
        monkeypatch,
        [400, 400, 400,
         ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"]],
        provider=LLMProvider.RELAY, model="gpt-5.6",
        relay_fallback="claude-opus-4-8,claude-sonnet-5",
        impl=OpenAIProvider(),
    )
    tokens = []
    async for tok in llm.stream(prompt="测试", system_prompt="s"):
        tokens.append(tok)
    assert tokens == ["ok"]
    assert fake.calls == 4
    models = [p.get("model") for p in fake.payloads]
    assert models == ["gpt-5.6"] * 3 + ["claude-opus-4-8"]
    # gpt-5 走 Responses API，思考档位取最低 minimal
    assert fake.payloads[0]["reasoning"] == {"effort": "minimal"}


async def test_stream_relay_fallback_exhausted(monkeypatch):
    """整条模型链都 400：3 模型 × 3 次重试 = 9 次后抛错"""
    llm, fake, _relay = await _make_llm(
        monkeypatch, [400],
        provider=LLMProvider.RELAY, model="gpt-5.6",
        relay_fallback="claude-opus-4-8,claude-sonnet-5",
        impl=OpenAIProvider(),
    )
    with pytest.raises(ServiceUnavailableError):
        async for _ in llm.stream(prompt="测试"):
            pass
    assert fake.calls == 9
    models = [p.get("model") for p in fake.payloads]
    assert models == ["gpt-5.6"] * 3 + ["claude-opus-4-8"] * 3 + ["claude-sonnet-5"] * 3


async def test_stream_no_fallback_after_yield(monkeypatch):
    """已产出 token 后断流：换模型重发会导致重复输出，必须直接抛错"""
    llm, fake, _relay = await _make_llm(
        monkeypatch,
        # gpt-5.6 走 Responses API，SSE 行须是 responses 事件格式
        [['data: {"type":"response.output_text.delta","delta":"hi"}']],
        provider=LLMProvider.RELAY, model="gpt-5.6",
        relay_fallback="claude-opus-4-8",
        impl=OpenAIProvider(),
    )
    tokens = []
    with pytest.raises(ServiceUnavailableError):
        async for tok in llm.stream(prompt="测试", system_prompt="s"):
            tokens.append(tok)
    assert tokens == ["hi"]
    # 只试了主模型一次，没有任何降级请求
    assert fake.calls == 1
    assert fake.payloads[0]["model"] == "gpt-5.6"


async def test_chat_relay_fallback(monkeypatch):
    """非流式 chat 同样降级：每模型 1 次非200即降级（无内部重试），gpt 400 → claude 返回正文"""
    llm, fake, _relay = await _make_llm(
        monkeypatch,
        [400, {"choices": [{"message": {"content": "答案"}}]}],
        provider=LLMProvider.RELAY, model="gpt-5.6",
        relay_fallback="claude-opus-4-8",
        impl=OpenAIProvider(),
    )
    out = await llm.chat([{"role": "user", "content": "测试"}])
    assert out == "答案"
    assert fake.calls == 2
    assert fake.payloads[-1]["model"] == "claude-opus-4-8"


async def test_stream_deepseek_never_falls_back(monkeypatch):
    """deepseek 直连不启用降级链（.env 配了 fallback 也无效）"""
    llm, fake, _relay = await _make_llm(
        monkeypatch, [400], relay_fallback="claude-opus-4-8")
    with pytest.raises(ServiceUnavailableError):
        async for _ in llm.stream(prompt="测试"):
            pass
    assert fake.calls == 3


# ---------- 首块业务数据超时（0916 中转 hang 心跳空转实锤） ----------

async def test_stream_first_chunk_timeout_retries_then_raises(monkeypatch):
    """心跳空转：SSE 200 但只发心跳/空行不吐业务块（0916 实锤用户干等 257s 的
    场景）——首块 deadline 触发 → 未产出 token 安全重试 3 次 → 全败抛 AITimeoutError
    （pipeline 捕获后给用户「服务暂时不可用」提示）。
    ("busy", sec) 用 time.sleep 真实流逝时钟（asyncio.sleep 被测试基建短路）"""
    llm, fake, _relay = await _make_llm(
        monkeypatch,
        [[("busy", 0.03), ": keepalive", ""],
         [("busy", 0.03), ": keepalive", ""],
         [("busy", 0.03), ": keepalive", ""]])
    llm.config.llm_stream_first_timeout = 0.01
    with pytest.raises(AITimeoutError):
        async for _ in llm.stream(prompt="测试"):
            pass
    assert fake.calls == 3


async def test_stream_first_chunk_timeout_recovers_on_retry(monkeypatch):
    """前两次首块超时，第三次正常吐块：超时熔断的重试能救回（对应 DeepSeek 抖动
    窗口「重试就过」的实际形态）"""
    llm, fake, _relay = await _make_llm(
        monkeypatch,
        [[("busy", 0.03), ": keepalive"],
         [("busy", 0.03), ": keepalive"],
         ['data: {"choices":[{"delta":{"content":"恢复"}}]}', "data: [DONE]"]])
    llm.config.llm_stream_first_timeout = 0.01
    tokens = []
    async for tok in llm.stream(prompt="测试"):
        tokens.append(tok)
    assert tokens == ["恢复"]
    assert fake.calls == 3


async def test_stream_reasoning_data_exempts_deadline(monkeypatch):
    """reasoning_content 也算业务数据：reasoner 长思考（首 content 前只流思考）
    不能被首块 deadline 误杀"""
    llm, fake, _relay = await _make_llm(
        monkeypatch,
        [['data: {"choices":[{"delta":{"reasoning_content":"思考中"}}]}',
          ("sleep", 0.3),
          'data: {"choices":[{"delta":{"content":"答案"}}]}',
          "data: [DONE]"]])
    tokens = []
    async for tok in llm.stream(prompt="测试"):
        tokens.append(tok)
    assert tokens == ["答案"]
    assert fake.calls == 1


# ---------- relay 思考开关（payload 构建） ----------

def test_build_payload_relay_thinking_switch():
    p = OpenAIProvider()
    # 默认：claude 不带 thinking 字段（中转站该字段支持不稳）
    payload = p.build_payload(model="claude-opus-4-8", messages=[], relay_thinking=False)
    assert "thinking" not in payload
    # RELAY_THINKING=on + 默认思考 → enabled + 最低预算（Anthropic 下限 1024）
    payload = p.build_payload(model="claude-opus-4-8", messages=[], relay_thinking=True)
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    # RELAY_THINKING=on + 显式 thinking=False → disabled
    payload = p.build_payload(model="claude-sonnet-5", messages=[], relay_thinking=True, thinking=False)
    assert payload["thinking"] == {"type": "disabled"}
    # 非 claude 模型不受开关影响
    payload = p.build_payload(model="gpt-4.8", messages=[], relay_thinking=True)
    assert "thinking" not in payload


def test_build_payload_no_relay_thinking_leak():
    """relay_thinking 是内部参数，不能泄漏进任何 payload"""
    p = DeepSeekProvider()
    payload = p.build_payload(model="deepseek-v4-flash", messages=[], relay_thinking=True)
    assert "relay_thinking" not in payload
    assert payload["thinking"] == {"type": "enabled"}
    p2 = OpenAIProvider()
    payload = p2.build_payload(model="claude-opus-4-8", messages=[], relay_thinking=True)
    assert "relay_thinking" not in payload


def test_deepseek_thinking_lowest_effort():
    """DeepSeek 思考开启时 reasoning_effort 一律最低档 low；关闭时不传"""
    p = DeepSeekProvider()
    payload = p.build_payload(model="deepseek-v4-flash", messages=[])
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "low"
    payload = p.build_payload(model="deepseek-v4-flash", messages=[], thinking=False)
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


def test_effort_floor():
    """gpt-5（Responses API）在 low 档位下取更低的 minimal"""
    llm = LLMClient.__new__(LLMClient)
    llm.reasoning_effort = "low"
    assert llm._effort_floor("gpt-5.6") == "minimal"
    assert llm._effort_floor("gpt-4.8") == "low"
    assert llm._effort_floor("o3") == "low"
    llm.reasoning_effort = None
    assert llm._effort_floor("gpt-5.6") is None
    llm.reasoning_effort = "high"
    assert llm._effort_floor("gpt-5.6") == "high"


# ---------- 跨站热备（1015：deepseek 官方崩 → 切中转 relay） ----------
# 背景：今早 deepseek 官方故障全站瘫痪。旧 _model_chain 在 provider=DEEPSEEK 时
# 硬编码单模型零降级。改 _failover_chain：主站崩（HTTP 错误/超时耗尽）→ 切中转
# RELAY_MODEL → 再崩 → RELAY_FALLBACK_MODELS。流式已产出 token 不切换。


def _ok_json(content="ok"):
    return {"choices": [{"message": {"content": content}}]}


def _sse(content="ok"):
    return [f'data: {{"choices":[{{"delta":{{"content":"{content}"}}}}]}}]', "data: [DONE]"]

def _sse_responses(content="ok"):
    """Responses API 事件流（gpt-5 系在中转站统一走 /responses，事件格式不同）"""
    return [
        f'data: {{"type":"response.output_text.delta","delta":"{content}"}}',
        'data: {"type":"response.completed"}',
    ]


async def test_failover_deepseek_500_to_relay_stream(monkeypatch):
    """主站流式持续 500 → 切中转 RELAY_MODEL 成功出 token"""
    llm, main, relay = await _make_llm(
        monkeypatch, [400, 400, 400],  # 主站 3 次重试全失败
        provider=LLMProvider.DEEPSEEK, model="deepseek-flash",
        relay_api_key="rk", relay_model="gpt-5.6-sol",
        relay_fallback="claude-sonnet-5",
        relay_scripts=[_sse_responses("来自中转")])
    tokens = []
    async for tok in llm.stream(prompt="测试", system_prompt="s"):
        tokens.append(tok)
    assert "".join(tokens) == "来自中转"
    assert relay.calls == 1, "主站失败后应恰好切换到中转一次"
    assert relay.payloads[0]["model"] == "gpt-5.6-sol"


async def test_failover_on_timeout_exhausted(monkeypatch):
    """主站连接类失败重试耗尽（AITimeoutError 形态）→ 同样切中转（1015 事故即超时形态）"""
    llm, main, relay = await _make_llm(
        monkeypatch, ["timeout", "timeout", "timeout"],
        provider=LLMProvider.DEEPSEEK, model="deepseek-flash",
        relay_api_key="rk", relay_model="gpt-5.6-sol",
        relay_scripts=[_sse_responses("备胎回复")])
    tokens = []
    async for tok in llm.stream(prompt="测试", system_prompt="s"):
        tokens.append(tok)
    assert "".join(tokens) == "备胎回复"


async def test_failover_chain_exhausted_raises(monkeypatch):
    """主站与中转全崩 → 模型链耗尽抛 ServiceUnavailableError"""
    llm, main, relay = await _make_llm(
        monkeypatch, [500, 500, 500],
        provider=LLMProvider.DEEPSEEK, model="deepseek-flash",
        relay_api_key="rk", relay_model="gpt-5.6-sol",
        relay_fallback="claude-sonnet-5",
        relay_scripts=[500, 500])
    with pytest.raises(ServiceUnavailableError):
        async for _ in llm.stream(prompt="测试", system_prompt="s"):
            pass
    assert relay.calls == 6, "中转每模型内层重试3次：gpt×3 + sonnet×3"


async def test_failover_no_switch_after_yielded(monkeypatch):
    """已产出 token 后主站断流 → 不切换（防重复输出），异常照抛"""
    llm, main, relay = await _make_llm(
        monkeypatch, [['data: {"choices":[{"delta":{"content":"前半"}}]}']],
        provider=LLMProvider.DEEPSEEK, model="deepseek-flash",
        relay_api_key="rk", relay_model="gpt-5.6-sol",
        relay_scripts=[_sse_responses("不该出现")])
    tokens = []
    with pytest.raises(ServiceUnavailableError):
        async for tok in llm.stream(prompt="测试", system_prompt="s"):
            tokens.append(tok)
    assert tokens == ["前半"]
    assert relay.calls == 0


async def test_failover_complete_non_stream(monkeypatch):
    """非流式 complete 同样跨站：主站 500 → 中转返回"""
    llm, main, relay = await _make_llm(
        monkeypatch, [500, 500, 500],
        provider=LLMProvider.DEEPSEEK, model="deepseek-flash",
        relay_api_key="rk", relay_model="gpt-5.6-sol",
        relay_scripts=[{"output": [{"type": "message", "content": [{"type": "output_text", "text": "中转补全"}]}]}])
    out = await llm.complete(prompt="测试", system_prompt="s")
    assert out == "中转补全"


def test_failover_chain_shape():
    """链构造：deepseek+relay key → 主+中转主模型+fallback；relay 主站 → 原语义；无 key → 单元素"""
    from unittest.mock import patch as _patch

    def _llm(provider, model, relay_key, fallback):
        llm = LLMClient.__new__(LLMClient)
        llm.provider = provider
        llm.model = model
        llm.config = SimpleNamespace(
            relay_api_key=relay_key, relay_model="gpt-5.6-sol",
            relay_fallback_models=fallback)
        return llm

    # deepseek 主站 + relay key：主 + 中转 gpt + claude（去重、不与主模型同名）
    llm = _llm(LLMProvider.DEEPSEEK, "deepseek-flash", "rk", "claude-opus-4-8,claude-sonnet-5")
    chain = llm._failover_chain()
    # 中转段 = RELAY_MODEL + fallback 原顺序（env: opus,sonnet）
    assert chain == [
        (LLMProvider.DEEPSEEK, "deepseek-flash"),
        (LLMProvider.RELAY, "gpt-5.6-sol"),
        (LLMProvider.RELAY, "claude-opus-4-8"),
        (LLMProvider.RELAY, "claude-sonnet-5"),
    ]

    # relay 主站：原语义（链内换模型，不叠加跨站）
    llm2 = _llm(LLMProvider.RELAY, "gpt-5.6-sol", "rk", "claude-sonnet-5")
    assert llm2._failover_chain() == [
        (LLMProvider.RELAY, "gpt-5.6-sol"),
        (LLMProvider.RELAY, "claude-sonnet-5"),
    ]

    # 无 relay key：单元素零降级（与旧行为一致）
    llm3 = _llm(LLMProvider.DEEPSEEK, "deepseek-flash", "", "")
    assert llm3._failover_chain() == [(LLMProvider.DEEPSEEK, "deepseek-flash")]
