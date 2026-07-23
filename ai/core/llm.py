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

logger = get_logger(__name__)

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
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        # 关闭思考模式加速首 token（deepseek、miMo 均支持）
        if any(x in model.lower() for x in ("deepseek", "mimo")):
            payload["thinking"] = {"type": "disabled"}
        return payload

    def extract_content(self, response: Dict[str, Any]) -> str:
        msg = response["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content", "")


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
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        # 关闭思考模式加速首 token（deepseek、miMo 均支持）
        if any(x in model.lower() for x in ("deepseek", "mimo")):
            payload["thinking"] = {"type": "disabled"}
        return payload

    def extract_content(self, response: Dict[str, Any]) -> str:
        msg = response["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content", "")


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
    ):
        self.config = get_ai_config()
        self.provider = provider
        self.api_key = api_key or self.config.deepseek_api_key
        self.base_url = base_url or self._get_default_base_url()
        self.model = model or self._get_default_model()
        self._provider_impl = _PROVIDERS[provider]
        self._client: Optional[httpx.AsyncClient] = None

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

    async def close(self) -> None:
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

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
    ) -> str:
        """
        同步补全（单轮对话）

        Args:
            prompt: 用户输入
            system_prompt: 系统提示
            max_tokens: 最大 token 数
            temperature: 温度参数

        Returns:
            LLM 生成的文本
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

    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        """
        多轮对话

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            max_tokens: 最大 token 数
            temperature: 温度参数

        Returns:
            LLM 生成的文本
        """
        try:
            response = await self._make_request(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
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
        max_tokens: int = 2000,
        temperature: float = 0.1,
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
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = self._provider_impl.build_payload(
            model=self.model, messages=messages,
            max_tokens=max_tokens, temperature=temperature, stream=True,
        )

        t_stream_start = time.perf_counter()
        has_yielded = False
        last_error = None
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
                                    # 优先取 content，思考模式下取 reasoning_content
                                    content = delta.get("content") or delta.get("reasoning_content", "")
                                    if content:
                                        if t_first_content is None:
                                            t_first_content = time.perf_counter()
                                            logger.debug(f"[llm-stream] first-token-from-http={((t_first_content-t_resp)*1000):.0f}ms")
                                        has_yielded = True
                                        yield content
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
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
