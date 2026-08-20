# 路径: ai/core/llm.py
"""
统一 LLM 接口层
- 支持多厂商切换（DeepSeek/OpenAI）
- 异步请求 + 重试机制
- 流式输出支持
"""
import asyncio
import json
import os
import time
from typing import Optional, List, Dict, Any
from abc import ABC, abstractmethod
from enum import Enum
import httpx

from ai.core.logging import get_logger

logger = get_logger("AI")

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)

from ai.config import get_ai_config
from ai.exceptions import AITimeoutError, ServiceUnavailableError


# ============================================================
# LLM Provider 抽象层
# ============================================================

class LLMProvider(Enum):
    """支持的 LLM 厂商"""
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    ZHIPU = "zhipu"
    RELAY = "relay"  # 中转站（OpenAI 兼容接口），当前用于试用 Claude 等备用模型


class BaseLLMProvider(ABC):
    """LLM Provider 基类"""

    @abstractmethod
    def get_api_url(self, base_url: str) -> str:
        """获取 API 端点"""
        pass

    @staticmethod
    def is_responses_model(model: str) -> bool:
        """是否走 Responses API。默认 False（chat completions）；
        OpenAIProvider 对 gpt-5 系列覆盖为 True。"""
        return False

    @staticmethod
    def get_responses_url(base_url: str) -> str:
        return f"{base_url}/responses"

    @abstractmethod
    def build_payload(
        self,
        model: str,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Dict[str, Any]:
        """构建请求 payload"""
        pass

    @abstractmethod
    def extract_content(self, response: Dict[str, Any]) -> str:
        """从响应中提取内容"""
        pass


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek provider"""

    def get_api_url(self, base_url: str) -> str:
        return f"{base_url}/chat/completions"

    def build_payload(
        self,
        model: str,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Dict[str, Any]:
        # 内部参数，不透传 API
        _thinking = kwargs.pop("thinking", None)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        # DeepSeek/miMo 思考模式（默认开启，传 thinking=False 显式关闭）
        if any(x in model.lower() for x in ("deepseek", "mimo")):
            if _thinking is False:
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["thinking"] = {"type": "enabled"}
        return payload

    def extract_content(self, response: Dict[str, Any]) -> str:
        msg = response["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content", "")

    @staticmethod
    def extract_reasoning(response: Dict[str, Any]) -> str:
        """提取思考过程（reasoning_content），可能为空"""
        msg = response["choices"][0]["message"]
        return msg.get("reasoning_content", "")


class OpenAIProvider(BaseLLMProvider):
    """OpenAI provider"""

    def get_api_url(self, base_url: str) -> str:
        return f"{base_url}/chat/completions"

    @staticmethod
    def is_responses_model(model: str) -> bool:
        """gpt-5 系列走 Responses API：Chat Completions 兼容层在中转站上整包缓冲
        （实测 gpt-5.6 stream=true 仍单块返回全部内容），Responses API 真逐字流。"""
        return model.lower().startswith("gpt-5")

    @staticmethod
    def get_responses_url(base_url: str) -> str:
        return f"{base_url}/responses"

    def build_payload(
        self,
        model: str,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Dict[str, Any]:
        # 内部参数，不透传 API
        _thinking = kwargs.pop("thinking", None)
        _reasoning_effort = kwargs.pop("reasoning_effort", None)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        _model = model.lower()
        # DeepSeek/miMo 思考模式（默认开启，传 thinking=False 显式关闭）
        if any(x in _model for x in ("deepseek", "mimo")):
            if _thinking is False:
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["thinking"] = {"type": "enabled"}
        # Claude 经 OpenAI 兼容中转站：**不传任何 thinking 字段**。
        # 实测（yitongapi 中转站 claude-opus-4-8）：只要请求带 thinking
        # （无论 enabled/disabled），思考型问题的正文字符就是 0——中转站对
        # claude 的 thinking 字段处理是坏的，不带参数反而最快且正常出答案。
        # 因此这里对 claude 一律忽略 thinking 开关，交给中转站默认行为。
        # reasoning_effort 只有 OpenAI 的推理模型（o1/o3/gpt-5 等）认识；
        # 中转站/Claude 等模型不一定兼容这个字段，不透传以免请求被拒。
        elif any(x in _model for x in ("o1", "o3", "gpt-5")):
            if _reasoning_effort:
                payload["reasoning_effort"] = _reasoning_effort
        return payload

    def extract_content(self, response: Dict[str, Any]) -> str:
        msg = response["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content", "")

    @staticmethod
    def extract_reasoning(response: Dict[str, Any]) -> str:
        """提取思考过程（reasoning_content），可能为空"""
        msg = response["choices"][0]["message"]
        return msg.get("reasoning_content", "")


# Provider 注册表
_PROVIDERS: Dict[LLMProvider, BaseLLMProvider] = {
    LLMProvider.DEEPSEEK: DeepSeekProvider(),
    LLMProvider.OPENAI: OpenAIProvider(),
    LLMProvider.RELAY: OpenAIProvider(),  # 中转站走标准 OpenAI 兼容格式
}


# ============================================================
# ============================================================
# 统一 LLM 客户端
# ============================================================

class LLMClient:
    """
    统一 LLM 客户端

    特性：
    - 多厂商支持（通过 provider 切换）
    - 异步 HTTP 请求
    - 重试机制（指数退避）
    - 响应缓存
    - 流式输出
    """

    def __init__(
        self,
        provider: LLMProvider = LLMProvider.DEEPSEEK,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ):
        self.config = get_ai_config()
        self.provider = provider
        if api_key:
            self.api_key = api_key
        elif provider == LLMProvider.RELAY:
            self.api_key = self.config.relay_api_key
        else:
            self.api_key = self.config.deepseek_api_key
        self.base_url = base_url or self._get_default_base_url()
        self.model = model or self._get_default_model()
        # 思考强度：显式传参 > .env 配置；off/disabled 表示关闭思考
        _effort = reasoning_effort if reasoning_effort is not None else self.config.llm_reasoning_effort
        if _effort and _effort.lower() in ("off", "disabled", ""):
            self.reasoning_effort = None
            self._thinking_default = False
        else:
            self.reasoning_effort = _effort or "low"
            self._thinking_default = True
        self._provider_impl = _PROVIDERS[provider]
        self._client: Optional[httpx.AsyncClient] = None
        self._vision_client: Optional[httpx.AsyncClient] = None

    def _get_default_base_url(self) -> str:
        """获取默认 base URL"""
        if self.provider == LLMProvider.DEEPSEEK:
            return self.config.deepseek_base_url
        elif self.provider == LLMProvider.OPENAI:
            return "https://api.openai.com/v1"
        elif self.provider == LLMProvider.RELAY:
            return self.config.relay_base_url
        return self.config.deepseek_base_url

    def _get_default_model(self) -> str:
        """获取默认模型"""
        if self.provider == LLMProvider.DEEPSEEK:
            return self.config.deepseek_model
        elif self.provider == LLMProvider.OPENAI:
            return "gpt-4o-mini"
        elif self.provider == LLMProvider.RELAY:
            return self.config.relay_model
        return self.config.deepseek_model

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=self.config.llm_connect_timeout,
                    read=self.config.llm_read_timeout,
                    write=10.0,
                    pool=10.0,
                ),
                trust_env=False,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    def _get_vision_client(self) -> httpx.AsyncClient:
        """获取或创建视觉客户端（复用连接池，避免每次请求重新建连）。

        与普通对话客户端不同，视觉请求可能并发执行（多图片/多会话），
        因此连接池上限放宽以支持多路复用；客户端常驻复用，减少重复 TCP+TLS 握手。
        """
        if self._vision_client is None or self._vision_client.is_closed:
            self._vision_client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.config.vision_api_key}",
                    "Content-Type": "application/json",
                },
                # connect/read 超时给足：大图 + 慢服务端一次请求常需数十秒
                timeout=httpx.Timeout(connect=45.0, read=90.0, write=60.0, pool=15.0),
                trust_env=False,
                # 维护多个 keepalive 连接，供并发视觉请求复用
                limits=httpx.Limits(
                    max_keepalive_connections=16,
                    max_connections=32,
                    keepalive_expiry=300.0,
                ),
            )
        return self._vision_client

    async def close(self) -> None:
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        if self._vision_client and not self._vision_client.is_closed:
            await self._vision_client.aclose()
            self._vision_client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type((
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        )),
        reraise=True,
    )
    async def _make_request(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Dict[str, Any]:
        """发送请求（带重试）"""
        client = await self._get_client()

        payload = self._provider_impl.build_payload(
            model=self.model,
            messages=messages,
            reasoning_effort=self.reasoning_effort,
            **kwargs,
        )

        url = self._provider_impl.get_api_url(self.base_url)
        response = await client.post(url, json=payload)

        if response.status_code == 401:
            raise ServiceUnavailableError("LLM", "API密钥无效或已过期")
        elif response.status_code == 429:
            raise ServiceUnavailableError("LLM", "请求频率超限，请稍后重试")
        elif response.status_code != 200:
            raise ServiceUnavailableError(
                "LLM", f"API返回错误码: {response.status_code}, body: {response.text[:200]}"
            )

        try:
            return response.json()
        except Exception as e:
            raise ServiceUnavailableError(
                "LLM",
                f"响应 JSON 解析失败({e})，status={response.status_code}，body 前200字符: {response.text[:200]}"
            )

    async def _complete_responses(
        self, prompt: str, system_prompt: Optional[str], max_tokens: int, temperature: float
    ) -> str:
        """gpt-5 走 Responses API 的非流式补全（标题生成/草稿生成等 complete 场景）。

        Chat Completions 兼容层在中转站上不流式且行为异常，gpt-5 统一走 /responses。
        """
        client = await self._get_client()
        _input = []
        if system_prompt:
            _input.append({"role": "system", "content": system_prompt})
        _input.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "input": _input,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        url = self._provider_impl.get_responses_url(self.base_url)
        response = await client.post(url, json=payload)
        if response.status_code != 200:
            raise ServiceUnavailableError(
                "LLM", f"API返回错误码: {response.status_code}, body: {response.text[:200]}"
            )
        try:
            data = response.json()
            # 原始 API 没有 output_text 顶层字段(SDK 便捷属性);顶层 text 是
            # 文本配置 dict({'format':..., 'verbosity':...}),不是正文。
            # 正文在: output[] → type=message → content[] → type=output_text → text
            out = ""
            for item in data.get("output") or []:
                if item.get("type") == "message":
                    for c in item.get("content") or []:
                        if c.get("type") == "output_text":
                            out += c.get("text") or ""
            return out
        except Exception as e:
            raise ServiceUnavailableError(
                "LLM",
                f"响应 JSON 解析失败({e})，status={response.status_code}，body 前200字符: {response.text[:200]}",
            )

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.1,
        thinking: bool = False,
    ) -> str:
        """
        同步补全（单轮对话）

        Args:
            prompt: 用户输入
            system_prompt: 系统提示
            max_tokens: 最大 token 数
            temperature: 温度参数
            thinking: 是否开启思考模式（工具调用默认关闭）
        """
        if self._provider_impl.is_responses_model(self.model):
            return await self._complete_responses(prompt, system_prompt, max_tokens, temperature)

        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._make_request(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking=thinking,
            )
            # 记录思考过程
            reasoning = self._provider_impl.extract_reasoning(response)
            if reasoning:
                logger.info(
                    f"[llm-reasoning] {len(reasoning)}chars: {reasoning[:500]}"
                    f"{'…' if len(reasoning) > 500 else ''}"
                )
            else:
                # 调试：检查消息级别是否有其他字段
                _msg = response.get("choices", [{}])[0].get("message", {})
                logger.info(
                    f"[llm-debug] no reasoning_content, "
                    f"msg_keys={list(_msg.keys())}, "
                    f"content_len={len(_msg.get('content', '') or '')}"
                )
            return self._provider_impl.extract_content(response)

        except RetryError as e:
            logger.error(f"LLM 请求超时（重试耗尽）: {e}", exc_info=True)
            raise AITimeoutError(f"LLM 服务响应超时: {str(e)}")
        except Exception as e:
            if isinstance(e, (AITimeoutError, ServiceUnavailableError)):
                raise
            logger.error(f"LLM 请求失败: {e}", exc_info=True)
            raise ServiceUnavailableError("LLM", f"请求失败: {str(e)}")

    async def complete_with_tools(
        self,
        tools: List[Dict[str, Any]],
        messages: Optional[List[Dict[str, Any]]] = None,
        prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.1,
        thinking: Optional[bool] = None,
    ) -> dict:
        """带工具的单轮补全：返回 {content, tool_calls, raw}。

        messages：完整消息列表（含历史 assistant tool_calls 与 tool 结果），
        多轮工具循环时用；prompt：单轮便捷参数（自动拼 system+user）。

        tool_calls: [{"id", "name", "arguments"(dict，已解析)}]
        无工具调用时 tool_calls 为空列表。
        LLM 只调工具不写正文时 content 为空字符串。
        失败抛 AITimeoutError / ServiceUnavailableError（与 complete 一致）。
        """
        if messages is None:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt or ""})

        response = await self._make_request(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking if thinking is not None else self._thinking_default,
            tools=tools,
        )
        _msg = response.get("choices", [{}])[0].get("message", {})
        tool_calls = []
        for tc in _msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": args,
            })
        reasoning = self._provider_impl.extract_reasoning(response)
        if reasoning:
            logger.info(f"[llm-reasoning] {len(reasoning)}chars: {reasoning[:300]}")
        return {
            "content": _msg.get("content") or "",
            "tool_calls": tool_calls,
            "raw": response,
        }

    async def complete_vision(
        self,
        prompt: str,
        images: List[str],
        system_prompt: Optional[str] = None,
        max_tokens: int = 3072,
        temperature: float = 0.3,
    ) -> str:
        """多模态补全：文本 + 图片 → 视觉 LLM（独立客户端，不走 DeepSeek）"""
        cfg = self.config
        t_start = time.perf_counter()

        img_sizes = []
        for img in images:
            if "base64," in img:
                b64_part = img.split("base64,", 1)[1] if "base64," in img else img
                img_sizes.append(len(b64_part))
            else:
                img_sizes.append(len(img))
        logger.info(
            f"[vision] 开始请求: model={cfg.vision_model}, images={len(images)}, "
            f"img_sizes={img_sizes}, prompt_len={len(prompt)}"
        )

        content_parts: list = [{"type": "text", "text": prompt}]
        for img in images:
            content_parts.append({"type": "image_url", "image_url": {"url": img}})

        messages: list = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        payload = {
            "model": cfg.vision_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        url = f"{cfg.vision_base_url}/v1/chat/completions"

        try:
            data = await self._vision_request(cfg, url=url, payload=payload, t_start=t_start)
            content = data["choices"][0]["message"].get("content", "")
            finish_reason = data["choices"][0].get("finish_reason", "?")
            usage = data.get("usage", {})
            logger.info(
                f"[vision] 请求成功: content_len={len(content)}, "
                f"finish_reason={finish_reason}, "
                f"usage={json.dumps(usage, ensure_ascii=False) if usage else 'N/A'}, "
                f"total={(time.perf_counter() - t_start) * 1000:.0f}ms"
            )
            if not content:
                logger.warning(
                    f"[vision] ⚠️ 返回空内容! finish_reason={finish_reason}, "
                    f"choices={json.dumps(data.get('choices', []), ensure_ascii=False)[:500]}"
                )
            return content

        except RetryError as e:
            elapsed = (time.perf_counter() - t_start) * 1000
            logger.error(
                f"[vision] 重试耗尽: elapsed={elapsed:.0f}ms, error={e}",
                exc_info=True,
            )
            raise AITimeoutError(f"Vision 服务响应超时: {str(e)}")
        except Exception as e:
            elapsed = (time.perf_counter() - t_start) * 1000
            if isinstance(e, (AITimeoutError, ServiceUnavailableError)):
                logger.error(f"[vision] 已知异常: type={type(e).__name__}, elapsed={elapsed:.0f}ms, error={e}")
                raise
            logger.error(f"[vision] 未知失败: type={type(e).__name__}, elapsed={elapsed:.0f}ms, error={e}", exc_info=True)
            raise ServiceUnavailableError("Vision", str(e)[:100])

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        retry=retry_if_exception_type((
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        )),
        reraise=True,
    )
    async def _vision_request(
        self,
        cfg,
        url: str,
        payload: dict,
        t_start: float,
    ) -> dict:
        """带重试的 Vision HTTP 请求（仅对瞬时网络错误重试）。

        复用常驻的视觉客户端连接池：多个 keepalive 连接可被并发请求复用，
        大幅降低每次请求重新 TCP+TLS 建连导致的 ConnectTimeout。
        """
        client = self._get_vision_client()
        resp = await client.post(url, json=payload)
        t_resp = time.perf_counter()
        logger.info(
            f"[vision] HTTP响应: status={resp.status_code}, "
            f"elapsed={(t_resp - t_start) * 1000:.0f}ms, resp_size={len(resp.text)}"
        )
        if resp.status_code != 200:
            logger.error(
                f"[vision] API错误: status={resp.status_code}, body前300字={resp.text[:300]}"
            )
            raise ServiceUnavailableError("Vision", f"API {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def stream_vision(
        self,
        prompt: str,
        images: List[str],
        system_prompt: Optional[str] = None,
        max_tokens: int = 3072,
        temperature: float = 0.3,
    ):
        """流式多模态补全：文本 + 图片 → 视觉 LLM，逐 token yield。

        复用常驻视觉客户端连接池；仅在连接阶段（未产出 token 前）尝试重试，
        避免已产出 token 后重试导致重复输出。
        """
        cfg = self.config
        t_start = time.perf_counter()

        content_parts: list = [{"type": "text", "text": prompt}]
        for img in images:
            content_parts.append({"type": "image_url", "image_url": {"url": img}})

        messages: list = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        payload = {
            "model": cfg.vision_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        url = f"{cfg.vision_base_url}/v1/chat/completions"

        client = self._get_vision_client()
        has_yielded = False
        last_error = None
        acc = []

        for attempt in range(3):
            try:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        raise ServiceUnavailableError(
                            "Vision", f"流式响应失败: {response.status_code}: {response.text[:200]}"
                        )
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                for choice in chunk.get("choices", []):
                                    delta = choice.get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        has_yielded = True
                                        acc.append(content)
                                        yield content
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                # 正常结束
                logger.info(
                    f"[vision-stream] 完成: content_len={sum(len(c) for c in acc)}, "
                    f"total={(time.perf_counter() - t_start) * 1000:.0f}ms"
                )
                return
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as e:
                last_error = e
                logger.warning(
                    f"[vision-stream] 重试: attempt={attempt + 1}, "
                    f"error={type(e).__name__}: {str(e)[:200]}"
                )
                if has_yielded or attempt == 2:
                    raise AITimeoutError(f"Vision 流式连接失败（重试{attempt + 1}次）: {str(e)}")
                await asyncio.sleep(min(5 * (2 ** attempt), 20))
            except Exception as e:
                if isinstance(e, (AITimeoutError, ServiceUnavailableError)):
                    raise
                logger.error(f"[vision-stream] 未知失败: type={type(e).__name__}, error={e}", exc_info=True)
                raise ServiceUnavailableError("Vision", str(e)[:100])

        if last_error:
            raise AITimeoutError(f"Vision 流式连接失败（重试3次）: {str(last_error)}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 2000,
        temperature: float = 0.1,
        thinking: bool = False,
    ) -> str:
        """
        多轮对话

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            max_tokens: 最大 token 数
            temperature: 温度参数
            thinking: 是否开启思考模式（工具调用默认关闭）
        """
        try:
            response = await self._make_request(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking=thinking,
            )
            # 记录思考过程
            reasoning = self._provider_impl.extract_reasoning(response)
            if reasoning:
                logger.info(
                    f"[llm-reasoning] {len(reasoning)}chars: {reasoning[:500]}"
                    f"{'…' if len(reasoning) > 500 else ''}"
                )
            return self._provider_impl.extract_content(response)

        except RetryError as e:
            logger.error(f"LLM 请求超时（重试耗尽）: {e}", exc_info=True)
            raise AITimeoutError(f"LLM 服务响应超时: {str(e)}")
        except Exception as e:
            if isinstance(e, (AITimeoutError, ServiceUnavailableError)):
                raise
            logger.error(f"LLM 请求失败: {e}", exc_info=True)
            raise ServiceUnavailableError("LLM", f"请求失败: {str(e)}")

    async def stream_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 2000,
        temperature: float = 0.2,
        thinking: Optional[bool] = None,
    ):
        """流式带工具补全：逐 token yield，流结束时 yield 完整的 tool_calls。

        yield 事件（dict）：
        - {"type": "token", "content": "..."}   正文 token（可实时转发前端）
        - {"type": "tool_calls", "tool_calls": [...], "content": "..."}
          流结束事件（含完整正文 + 解析好的 tool_calls，arguments 已是 dict）

        工具调用的流式特性：tool_calls 的 id/name/arguments 按 fragment 增量到达，
        需要按 index 累积拼接；arguments 在流结束时才是完整 JSON。
        失败策略与 stream 一致：连接阶段（未产出任何内容）重试最多 3 次。
        """
        if thinking is None:
            thinking = self._thinking_default
        payload = self._provider_impl.build_payload(
            model=self.model, messages=messages,
            max_tokens=max_tokens, temperature=temperature, stream=True,
            reasoning_effort=self.reasoning_effort, thinking=thinking,
            tools=tools,
        )

        has_yielded = False
        last_error = None
        reasoning_parts: list[str] = []
        for attempt in range(3):
            try:
                client = await self._get_client()
                url = self._provider_impl.get_api_url(self.base_url)
                full_content: list[str] = []
                tool_fragments: Dict[int, Dict[str, str]] = {}
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise ServiceUnavailableError(
                            "LLM",
                            f"流式响应失败: {response.status_code}, body: {body[:300].decode('utf-8', 'replace')}",
                        )
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    reasoning_token = delta.get("reasoning_content", "")
                                    if reasoning_token:
                                        reasoning_parts.append(reasoning_token)
                                    content = delta.get("content", "")
                                    if content:
                                        has_yielded = True
                                        full_content.append(content)
                                        yield {"type": "token", "content": content}
                                    # 工具调用 fragment 累积
                                    for tc in delta.get("tool_calls") or []:
                                        idx = tc.get("index", 0)
                                        frag = tool_fragments.setdefault(
                                            idx, {"id": "", "name": "", "arguments": ""})
                                        if tc.get("id"):
                                            frag["id"] += tc["id"]
                                        fn = tc.get("function") or {}
                                        if fn.get("name"):
                                            # 函数名不是增量文本；中转站 Claude 可能在多个
                                            # chunk 重复发送完整名称，不能用 +=，否则会变成
                                            # search_kbsearch_kb...，随后被工具循环判为未知工具。
                                            frag["name"] = fn["name"]
                                        if fn.get("arguments"):
                                            frag["arguments"] += fn["arguments"]
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                    if reasoning_parts:
                        full_reasoning = "".join(reasoning_parts)
                        logger.info(
                            f"[llm-reasoning] {len(full_reasoning)}chars: {full_reasoning[:300]}"
                        )
                # 流结束：组装 tool_calls
                tool_calls = []
                for idx in sorted(tool_fragments.keys()):
                    frag = tool_fragments[idx]
                    if not frag["name"]:
                        continue
                    args = {}
                    try:
                        args = json.loads(frag["arguments"] or "{}")
                    except Exception:
                        args = {}
                    tool_calls.append({"id": frag["id"], "name": frag["name"], "arguments": args})
                yield {"type": "tool_calls", "tool_calls": tool_calls,
                       "content": "".join(full_content)}
                return
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as e:
                last_error = e
                logger.warning(f"LLM 流式重试: attempt={attempt+1}, error={type(e).__name__}: {str(e)[:200]}")
                if has_yielded or attempt == 2:
                    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
                        raise AITimeoutError(f"LLM 流式连接失败（重试{attempt+1}次）: {str(e)}")
                    raise ServiceUnavailableError("LLM", f"流式连接失败: {str(e)}")
                await asyncio.sleep(min(1 * (2 ** attempt), 4))

        if last_error:
            raise AITimeoutError(f"LLM 流式连接失败（重试3次）: {str(last_error)}")

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        temperature: float = 0.1,
        thinking: Optional[bool] = None,
    ):
        """流式补全 — 开启 stream:true，逐 token yield

        Example:
            gen = await llm.stream("你好")
            async for token in gen:
                print(token, end="")

        注意：流式 SSE 无法在已开始产出 token 后安全重试（会导致重复输出），
        因此仅在连接建立阶段（无 token 产出时）尝试重试，最多 3 次。
        """
        if thinking is None:
            thinking = self._thinking_default
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = self._provider_impl.build_payload(
            model=self.model, messages=messages,
            max_tokens=max_tokens, temperature=temperature, stream=True,
            reasoning_effort=self.reasoning_effort, thinking=thinking,
        )
        _use_responses = self._provider_impl.is_responses_model(self.model)
        if _use_responses:
            payload = {
                "model": self.model,
                "input": messages if system_prompt else prompt,
                "stream": True,
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }
            if self.reasoning_effort:
                payload["reasoning"] = {"effort": self.reasoning_effort}

        t_stream_start = time.perf_counter()
        has_yielded = False
        last_error = None
        reasoning_parts: list[str] = []
        _debug_first_delta = True
        for attempt in range(3):
            try:
                client = await self._get_client()
                url = (self._provider_impl.get_responses_url(self.base_url)
                       if _use_responses else self._provider_impl.get_api_url(self.base_url))
                t_conn = time.perf_counter()
                async with client.stream("POST", url, json=payload) as response:
                    t_resp = time.perf_counter()
                    logger.debug(f"[llm-internal] connect={((t_conn-t_stream_start)*1000):.0f}ms  "
                                 f"resp_wait={((t_resp-t_conn)*1000):.0f}ms  "
                                 f"status={response.status_code}")
                    if response.status_code != 200:
                        body = await response.aread()
                        raise ServiceUnavailableError(
                            "LLM",
                            f"流式响应失败: {response.status_code}, body: {body[:300].decode('utf-8', 'replace')}",
                        )

                    t_first_content = None
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                if _use_responses:
                                    # Responses API 事件流:response.output_text.delta 是正文
                                    _ev_type = chunk.get("type")
                                    if _ev_type == "response.output_text.delta":
                                        content = chunk.get("delta") or ""
                                    elif _ev_type == "response.completed":
                                        break
                                    else:
                                        content = ""
                                    if content:
                                        if t_first_content is None:
                                            t_first_content = time.perf_counter()
                                            logger.debug(f"[llm-stream] first-token-from-http={((t_first_content-t_resp)*1000):.0f}ms")
                                        has_yielded = True
                                        yield content
                                    continue
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    # 调试：首 chunk 打印 delta 所有字段名
                                    if _debug_first_delta:
                                        _debug_first_delta = False
                                        logger.info(
                                            f"[llm-stream-debug] first_delta_keys={list(delta.keys())} "
                                            f"sample={str(delta)[:300]}"
                                        )
                                    # 思考内容：只记录不输出
                                    reasoning_token = delta.get("reasoning_content", "")
                                    if reasoning_token:
                                        reasoning_parts.append(reasoning_token)
                                    # 正式回复内容：输出给调用方
                                    content = delta.get("content", "")
                                    if content:
                                        if t_first_content is None:
                                            t_first_content = time.perf_counter()
                                            logger.debug(f"[llm-stream] first-token-from-http={((t_first_content-t_resp)*1000):.0f}ms")
                                        has_yielded = True
                                        yield content
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                    # 流正常结束，记录思考过程
                    if reasoning_parts:
                        full_reasoning = "".join(reasoning_parts)
                        logger.info(
                            f"[llm-reasoning-stream] {len(full_reasoning)}chars: "
                            f"{full_reasoning[:500]}"
                            f"{'…' if len(full_reasoning) > 500 else ''}"
                        )
                return  # 正常结束
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as e:
                last_error = e
                logger.warning(f"LLM 流式重试: attempt={attempt+1}, error={type(e).__name__}: {str(e)[:200]}")
                if has_yielded or attempt == 2:
                    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
                        raise AITimeoutError(f"LLM 流式连接失败（重试{attempt+1}次）: {str(e)}")
                    raise ServiceUnavailableError("LLM", f"流式连接失败: {str(e)}")
                await asyncio.sleep(min(1 * (2 ** attempt), 4))

        if last_error:
            raise AITimeoutError(f"LLM 流式连接失败（重试3次）: {str(last_error)}")


# ============================================================
# 全局客户端单例
# ============================================================

_llm_client: Optional[LLMClient] = None
_client_lock = asyncio.Lock()


def _resolve_default_provider() -> LLMProvider:
    """根据 .env 的 LLM_BACKEND 决定全局默认走哪个 provider（deepseek/relay/openai）。"""
    backend = (get_ai_config().llm_backend or "deepseek").strip().lower()
    if backend == "relay":
        return LLMProvider.RELAY
    if backend == "openai":
        return LLMProvider.OPENAI
    return LLMProvider.DEEPSEEK


async def get_llm_client(
    provider: Optional[LLMProvider] = None,
) -> LLMClient:
    """
    获取 LLM 客户端单例

    Args:
        provider: LLM 厂商；不传时按 .env 的 LLM_BACKEND 决定（默认 DeepSeek）

    Returns:
        LLMClient 实例
    """
    global _llm_client

    if provider is None:
        provider = _resolve_default_provider()

    if _llm_client is None:
        async with _client_lock:
            if _llm_client is None:
                _llm_client = LLMClient(provider=provider)

    return _llm_client


async def close_llm_client() -> None:
    """关闭 LLM 客户端"""
    global _llm_client

    if _llm_client is not None:
        await _llm_client.close()
        _llm_client = None


# ── 意图分类专用客户端 ──────────────────────────────────────
# 意图识别是纯路由判断（courtesy/ticket/diagnosis），按设计用轻量无思考模型
# （v4-flash ~0.5s，见 _classify_intent 注释）。不能跟随主 LLM_BACKEND——
# 主后端切到 relay 的重模型（claude-opus）后，意图延迟从 0.5s 涨到 2s+。
# 默认独立走 DeepSeek 官方 API；INTENT_LLM_BACKEND / INTENT_MODEL 可覆盖；
# DeepSeek key 未配置时回退主客户端（保证意图功能不因配置缺失而断）。

_intent_client: Optional[LLMClient] = None
_intent_lock = asyncio.Lock()


async def get_intent_client() -> LLMClient:
    """意图分类专用客户端（轻量无思考模型，独立于主后端）。"""
    global _intent_client

    if _intent_client is None:
        async with _intent_lock:
            if _intent_client is None:
                backend = (os.getenv("INTENT_LLM_BACKEND") or "deepseek").strip().lower()
                model = (os.getenv("INTENT_MODEL") or "deepseek-v4-flash").strip()
                if backend == "deepseek":
                    if not get_ai_config().deepseek_api_key:
                        logger.warning("[intent] DeepSeek key 未配置，意图回退主客户端")
                        _intent_client = await get_llm_client()
                    else:
                        _intent_client = LLMClient(
                            provider=LLMProvider.DEEPSEEK, model=model)
                elif backend == "relay":
                    _intent_client = LLMClient(provider=LLMProvider.RELAY, model=model)
                else:
                    _intent_client = await get_llm_client()
    return _intent_client


# ============================================================
# 便捷函数
# ============================================================

async def complete(
    prompt: str,
    system_prompt: Optional[str] = None,
    **kwargs
) -> str:
    """快捷调用 complete"""
    client = await get_llm_client()
    return await client.complete(prompt, system_prompt, **kwargs)


async def chat(
    messages: List[Dict[str, str]],
    **kwargs
) -> str:
    """快捷调用 chat"""
    client = await get_llm_client()
    return await client.chat(messages, **kwargs)
