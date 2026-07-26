"""WeChat QA → Ticket E2E flow helper."""

from typing import Any, Dict


class WeChatQAFlow:
    """Reusable QA → conversation → ticket operations."""

    @staticmethod
    async def ask_question(ctx, auth_header: str, question: str) -> Dict[str, Any]:
        r = await ctx["client"].request("POST", "/api/qa/ask",
                                          headers=ctx[auth_header],
                                          json={"question": question})
        assert r.status_code == 200
        return r.json()

    @staticmethod
    async def create_conversation(ctx, auth_header: str,
                                   title: str = "E2E conversation") -> Dict[str, Any]:
        r = await ctx["client"].request("POST", "/api/conversations",
                                          headers=ctx[auth_header],
                                          json={"title": title})
        assert r.status_code == 200
        return r.json()
