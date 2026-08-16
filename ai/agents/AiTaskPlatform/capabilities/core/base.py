"""BaseCapability 抽象基类 + CapabilityResult 统一返回

借鉴主流 Agent 框架（LangChain/CrewAI/Claude/MetaGPT）的工具层思想：
  - name/description/schema/run 能力元数据"三角结构"（主流共识）
  - CrewAI `__init_subclass__` 子类自动注册
  - LangChain `handle_tool_error`：错误态返回而非抛异常中断 Agent
  - CrewAI `result_schema`：输出也 schema 化
  - MetaGPT `name` 默认取类名
  - Anthropic ACI：description 像写 docstring 一样用心（何时用/何时不用/示例/边界）

实现方式：`CapabilityResult` 用轻量 dataclass（结果容器，支持位置参数）；
`input_schema`/`result_schema` 用现有 Pydantic v2（与 ai/agents/AiTaskPlatform/schemas.py 一致）。
注意：本环境 Pydantic 2.13 禁止位置参数构造 BaseModel，只用于 schema，不用来构造结果。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry


class CapabilityResult:
    """统一能力返回：内容 + 元信息 + 状态（供 trace / Evaluator / Router 使用）。

    用 dataclass（非 Pydantic）：它是轻量结果容器，构造频繁，且支持位置参数
    （本环境 Pydantic 2.13 禁止位置参数构造 BaseModel，见 BUG 记录）。

    - text: 注入 prompt 的文本（给 LLM 看）
    - meta: 结构化信息（给程序看：line 数 / confidence / 耗时 ...）
    - ok:   执行是否成功（借鉴 LangChain 错误态返回，失败不中断 Agent）
    - error: 失败原因（ok=False 时，对外只暴露轻量原因，不泄漏内部堆栈）
    """

    __slots__ = ("text", "meta", "ok", "error")

    def __init__(self, text: str = "", meta: Optional[dict] = None, ok: bool = True, error: Optional[str] = None):
        self.text = text
        self.meta = meta or {}
        self.ok = ok
        self.error = error

    @classmethod
    def failure(cls, message: str) -> "CapabilityResult":
        """构造失败结果（错误态返回，不抛异常中断编排）。"""
        return cls(text="", meta={}, ok=False, error=message)

    def to_dict(self) -> dict:
        """转 dict（供 Supervisor/tracing 展示）。"""
        return {"text": self.text, "meta": self.meta, "ok": self.ok, "error": self.error}


class BaseCapability(ABC):
    """能力抽象基类。所有能力继承并实现 run()。

    子类继承即可被 `CapabilityRegistry` 自动注册（见 __init_subclass__），无需手工登记。
    """

    # ── 能力元数据（子类覆盖）──
    name: str = ""               # 能力名（默认取类名，借鉴 MetaGPT）
    description: str = ""        # 给 LLM 看的能力描述（Anthropic ACI 风格：何时用/何时不用/示例/边界）
    input_schema: Optional[type[BaseModel]] = None  # 输入校验模型（Pydantic），None = 免校验
    result_schema: Optional[type[BaseModel]] = None # 输出元数据 schema（可选，借鉴 CrewAI）
    tags: list[str] = []         # 标签：["log","image","code","history","knowledge"...]

    # ── 生命周期 / 配额（可选）──
    max_usage_per_session: Optional[int] = None  # 单会话调用上限（None=不限），借鉴 CrewAI max_usage_count

    def __init__(self):
        # 会话内调用计数（配额）
        self._usage_count = 0

    # ── 子类必须实现 ──
    @abstractmethod
    async def run(self, **kwargs: Any) -> CapabilityResult:
        """执行能力，返回统一 CapabilityResult。

        建议实现内部 try/except，失败用 `CapabilityResult.failure(msg)` 返回，
        避免抛异常中断 Agent 编排（借鉴 LangChain 错误态）。
        """
        raise NotImplementedError

    # ── 环境敏感性（可选覆写）──
    def is_available(self) -> bool:
        """该能力在当前环境是否可用（默认 True）。

        环境敏感能力（如 code_skill 需 CODE_SKILL_PATHS）应覆写，返回 False 则注册表
        list_available() 不暴露它，Router/Orchestrator 不会调度。
        """
        return True

    # ── 配额控制 ──
    def _claim_usage(self) -> bool:
        """尝试占用一次调用额度。超限返回 False。"""
        if self.max_usage_per_session is None:
            return True
        if self._usage_count >= self.max_usage_per_session:
            return False
        self._usage_count += 1
        return True

    # ── 统一执行入口（带配额 + 异常兜底）──
    async def __call__(self, **kwargs: Any) -> CapabilityResult:
        """带配额控制 + 异常兜底的统一执行入口。

        调用方统一用 `await capability(**kwargs)`（或 `capability.run(...)`），
        这里负责：配额检查 → run() → 异常转错误态。
        """
        # 配额检查
        if not self._claim_usage():
            return CapabilityResult.failure(
                f"能力 {self.name} 已达单会话调用上限 ({self.max_usage_per_session})"
            )
        # 环境可用性
        if not self.is_available():
            return CapabilityResult.failure(f"能力 {self.name} 在当前环境不可用")
        # 输入校验（若有 input_schema）
        if self.input_schema is not None:
            try:
                kwargs = dict(self.input_schema(**kwargs))  # Pydantic 校验+转换
            except Exception as e:
                return CapabilityResult.failure(f"能力 {self.name} 输入校验失败: {e}")
        # 执行
        try:
            return await self.run(**kwargs)
        except Exception as e:
            # 借鉴 LangChain：只暴露异常类型/轻量消息，不泄漏内部堆栈
            return CapabilityResult.failure(f"能力 {self.name} 执行失败: {type(e).__name__}: {e}")

    # ── 自动注册 ──
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """子类继承即自动注册到注册表（借鉴 CrewAI，key 用 module.QualifiedName 保证唯一）。

        注册发生在模块 import 时（定义类即触发），无需手工调用。
        """
        super().__init_subclass__(**kwargs)
        if not cls.name:
            cls.name = cls.__name__  # 借鉴 MetaGPT：name 默认取类名
        # 延后 import 避免循环依赖
        from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
        CapabilityRegistry.register(cls)
