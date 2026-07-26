"""Tests for LLMClient."""

import pytest
from automation.ai.utils.llm_client import LLMClient


class TestLLMClientMock:
    """LLMClient in mock mode."""

    @pytest.fixture
    def client(self) -> LLMClient:
        return LLMClient(mock=True)

    async def test_chat_returns_string(self, client: LLMClient):
        result = await client.chat([{"role": "user", "content": "Hello"}])
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_chat_error_keyword(self, client: LLMClient):
        result = await client.chat([{"role": "user", "content": "What does error E1001 mean?"}])
        assert "error" in result.lower() or "fault" in result.lower()

    async def test_chat_reset_keyword(self, client: LLMClient):
        result = await client.chat([{"role": "user", "content": "How to reset my robot?"}])
        assert "reset" in result.lower()

    async def test_chat_default_response(self, client: LLMClient):
        result = await client.chat([{"role": "user", "content": "Tell me a story"}])
        assert "mock" in result.lower()

    async def test_chat_stream(self, client: LLMClient):
        chunks = []
        async for chunk in client.chat_stream([{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)
        assert len(chunks) >= 1
        assert isinstance(chunks[0], str)

    async def test_empty_messages(self, client: LLMClient):
        result = await client.chat([])
        assert isinstance(result, str)
