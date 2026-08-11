# 路径: ai/core/llm.py
"""
统一 LLM 接口层
- 支持多厂商切换（DeepSeek/OpenAI）
- 异步请求 + 重试机制
- 流式输出支持
"""
import asyncio
import json
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


class BaseLLMProvider(ABC):
    """LLM Provider 基类"""

    @abstractmethod
    def get_api_url(self, base_url: str) -> str:
        """获取 API 端点"""
        pass

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


# Provider 注册表
_PROVIDERS: Dict[LLMProvider, BaseLLMProvider] = {
    LLMProvider.DEEPSEEK: DeepSeekProvider(),
    LLMProvider.OPENAI: OpenAIProvider(),
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
        self.api_key = api_key or self.config.deepseek_api_key
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
        return self.config.deepseek_base_url

    def _get_default_model(self) -> str:
        """获取默认模型"""
        if self.provider == LLMProvider.DEEPSEEK:
            return self.config.deepseek_model
        elif self.provider == LLMProvider.OPENAI:
            return "gpt-4o-mini"
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

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        temperature: float = 0.1,
        thinking: Optional[bool] = None,
    ):
        """
        流式补全 — 开启 stream:true，逐 token yield

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

        t_stream_start = time.perf_counter()
        has_yielded = False
        last_error = None
        reasoning_parts: list[str] = []
        _debug_first_delta = True
        for attempt in range(3):
            try:
                client = await self._get_client()
                url = self._provider_impl.get_api_url(self.base_url)
                t_conn = time.perf_counter()
                async with client.stream("POST", url, json=payload) as response:
                    t_resp = time.perf_counter()
                    logger.debug(f"[llm-internal] connect={((t_conn-t_stream_start)*1000):.0f}ms  "
                                 f"resp_wait={((t_resp-t_conn)*1000):.0f}ms  "
                                 f"status={response.status_code}")
                    if response.status_code != 200:
                        raise ServiceUnavailableError("LLM", f"流式响应失败: {response.status_code}")

                    t_first_content = None
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
                print(f"  ⚠️  [llm] stream retry attempt={attempt+1} err={type(e).__name__}: {str(e)[:100]}")
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


async def get_llm_client(
    provider: LLMProvider = LLMProvider.DEEPSEEK,
) -> LLMClient:
    """
    获取 LLM 客户端单例

    Args:
        provider: LLM 厂商（默认 DeepSeek）

    Returns:
        LLMClient 实例
    """
    global _llm_client

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
