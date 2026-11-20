"""BaseCapability 抽象基类 + CapabilityResult 统一返回

能力元数据"三角结构"：name/description/schema/run；子类继承即自动注册到注册表；
错误态返回而非抛异常中断 Agent；description 描述何时用/何时不用/示例/边界。

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
    """统一能力返回：内容 + 元信息 + 状态（供 trace / Router 使用）。

    用 dataclass（非 Pydantic）：它是轻量结果容器，构造频繁，且支持位置参数
    （本环境 Pydantic 2.13 禁止位置参数构造 BaseModel，见 BUG 记录）。

    - text: 注入 prompt 的文本（给 LLM 看）
    - meta: 结构化信息（给程序看：line 数 / confidence / 耗时 ...）
    - ok:   执行是否成功（错误态返回，失败不中断 Agent）
    - error: 失败原因（ok=False 时，对外只暴露轻量原因，不泄漏内部堆栈）
    """

    __slots__ = ("text", "meta", "ok", "error", "terminate")

    def __init__(
        self,
        text: str = "",
        meta: Optional[dict] = None,
        ok: bool = True,
        error: Optional[str] = None,
        terminate: bool = False,
    ):
        self.text = text
        self.meta = meta or {}
        self.ok = ok
        self.error = error
        self.terminate = bool(terminate)

    @classmethod
    def failure(cls, message: str) -> "CapabilityResult":
        """构造失败结果（错误态返回，不抛异常中断编排）。"""
        return cls(text="", meta={}, ok=False, error=message)

    def to_dict(self) -> dict:
        """转 dict（供 Supervisor/tracing 展示）。"""
        return {
            "text": self.text,
            "meta": self.meta,
            "ok": self.ok,
            "error": self.error,
            "terminate": self.terminate,
        }


class BaseCapability(ABC):
    """能力抽象基类。所有能力继承并实现 run()。

    子类继承即可被 `CapabilityRegistry` 自动注册（见 __init_subclass__），无需手工登记。
    """

    # ── 能力元数据（子类覆盖）──
    name: str = ""               # 能力名（默认取类名）
    description: str = ""        # 给 LLM 看的能力描述（何时用/何时不用/示例/边界）
    input_schema: Optional[type[BaseModel]] = None  # 输入校验模型（Pydantic），None = 免校验
    result_schema: Optional[type[BaseModel]] = None # 输出元数据 schema（可选）
    tags: list[str] = []         # 标签：["log","image","code","history","knowledge"...]

    # ── 生命周期 / 配额（可选）──
    # 本次请求（一轮讨论 / 一次诊断）内，该能力被【LLM 自主调度】的次数上限。
    # 由 supervisor 在机制层强制：超出即不排进计划、也不再派发，并把「已达上限」写进
    # 汇总正文（见 capabilities/core/usage.py）。**不要在实例上计数**——能力实例每次
    # 新建（registry.get → cap_cls()），挂在实例上的计数永远是 0（见 issues.md #10）。
    # 代码里确定性的兜底调用（如 discuss_flow 按用户要求补做日志分析）不计入此限。
    max_usage_per_session: Optional[int] = None
    requires_approval: bool = False  # True 时 before_run 按审批策略短路，读日志/知识库保持 False

    # ── 子类必须实现 ──
    @abstractmethod
    async def run(self, **kwargs: Any) -> CapabilityResult:
        """执行能力，返回统一 CapabilityResult。

        建议实现内部 try/except，失败用 `CapabilityResult.failure(msg)` 返回，
        避免抛异常中断 Agent 编排。
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
    async def before_run(self, **kwargs: Any) -> Optional[CapabilityResult]:
        """执行前钩子。审批策略不通过时返回结果并短路；否则 None，继续 run。"""
        from ai.agents.AiTaskPlatform.capabilities.core.approval import approval_gate
        return approval_gate(self, kwargs)

    async def after_run(self, result: CapabilityResult, **kwargs: Any) -> CapabilityResult:
        """执行后钩子：可改写/盖章结果；默认原样返回。异常由调用方忽略后处理。"""
        return result

    # ── 统一执行入口（异常兜底）──
    async def __call__(self, **kwargs: Any) -> CapabilityResult:
        """带异常兜底的统一执行入口。

        调用方统一用 `await capability(**kwargs)`（或 `capability.run(...)`），
        这里负责：before_run → run → after_run → 异常转错误态。

        配额（`max_usage_per_session`）**不在这里管**：它由 supervisor 按请求计数并
        在派发前拦下（capabilities/core/usage.py），因为实例每次新建、计数挂不住。
        """
        # 环境可用性
        if not self.is_available():
            return CapabilityResult.failure(f"能力 {self.name} 在当前环境不可用")
        # 输入校验（若有 input_schema）
        if self.input_schema is not None:
            try:
                kwargs = dict(self.input_schema(**kwargs))  # Pydantic 校验+转换
            except Exception as e:
                return CapabilityResult.failure(f"能力 {self.name} 输入校验失败: {e}")
        # 执行前钩子（可短路）
        try:
            early = await self.before_run(**kwargs)
            if early is not None:
                return early
        except Exception as e:
            return CapabilityResult.failure(
                f"能力 {self.name} before_run 失败: {type(e).__name__}: {e}"
            )
        # 执行
        try:
            result = await self.run(**kwargs)
        except Exception as e:
            # 只暴露异常类型/轻量消息，不泄漏内部堆栈
            return CapabilityResult.failure(f"能力 {self.name} 执行失败: {type(e).__name__}: {e}")
        # 执行后钩子（失败不吞掉主结果）
        try:
            return await self.after_run(result, **kwargs)
        except Exception as e:
            from ai.core.logging import get_logger
            get_logger("TASK_AGENT").warning(
                f"能力 {self.name} after_run 失败（保留原结果）: {type(e).__name__}: {e}"
            )
            return result

    # ── 自动注册 ──
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """子类继承即自动注册到注册表（key 用 module.QualifiedName 保证唯一）。

        注册发生在模块 import 时（定义类即触发），无需手工调用。
        """
        super().__init_subclass__(**kwargs)
        if not cls.name:
            cls.name = cls.__name__  # name 默认取类名
        # 延后 import 避免循环依赖
        from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
        CapabilityRegistry.register(cls)
