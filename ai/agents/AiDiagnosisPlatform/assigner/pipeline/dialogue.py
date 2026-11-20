"""一次派单共用的多轮对话。后一步调用模型时带上前面各轮的原文。"""

from __future__ import annotations


class DispatchDialogue:
    def __init__(self):
        self.messages: list[dict] = []

    def add(self, role: str, content: str) -> None:
        text = (content or "").strip()
        if text:
            self.messages.append({"role": role, "content": text})

    def note(self, title: str, body: str) -> None:
        """没有调用模型的步骤，把结论写成一轮用户发言。"""
        text = (body or "").strip()
        if text:
            self.add("user", f"【{title}】\n{text}")

    async def complete(self, llm, prompt: str, **kwargs) -> str:
        """已有轮次时走公共 chat；第一轮仍走 complete。成功后再记入本轮问答。"""
        text = (prompt or "").strip()
        if not text:
            return ""
        if self.messages:
            messages = list(self.messages) + [{"role": "user", "content": text}]
            response = await llm.chat(messages, **kwargs)
        else:
            response = await llm.complete(text, **kwargs)
        self.add("user", text)
        self.add("assistant", response or "")
        return response or ""
