"""CodeSkill 数据模型"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FunctionRef:
    """一个函数/方法/类的索引条目"""
    name: str               # 函数名
    file_path: str           # 相对项目根路径
    line_start: int          # 起始行
    line_end: int            # 结束行
    signature: str           # 完整签名 "async def discuss(self, task_id: str) -> dict:"
    docstring: str           # 文档注释（前200字）
    calls: List[str] = field(default_factory=list)      # 我调了谁（函数名列表）
    called_by: List[str] = field(default_factory=list)  # 谁调了我
    language: str = "python"  # python | typescript | other


@dataclass
class CodeSearchResult:
    """一次代码检索的结果"""
    query: str
    matches: List[FunctionRef] = field(default_factory=list)
    # 沿调用图展开的上下游
    upstream: List[FunctionRef] = field(default_factory=list)
    downstream: List[FunctionRef] = field(default_factory=list)

    def to_prompt_text(self, max_depth: int = 2) -> str:
        """生成供 LLM Prompt 注入的文本"""
        lines = []
        if self.matches:
            lines.append("## 匹配到的函数")
            for f in self.matches[:5]:
                lines.append(f"- `{f.signature}` ({f.file_path}:{f.line_start})")
                if f.docstring and f.docstring != "(无)":
                    lines.append(f"  {f.docstring[:200]}")
        if self.upstream:
            lines.append(f"\n## 上游调用方（谁调了这些函数）")
            for f in self.upstream[:3]:
                lines.append(f"- `{f.signature}` ({f.file_path}:{f.line_start})")
        if self.downstream:
            lines.append(f"\n## 下游被调用方（这些函数调了谁）")
            for f in self.downstream[:5]:
                lines.append(f"- `{f.signature}` ({f.file_path}:{f.line_start})")
        if not self.matches:
            lines.append("(未找到匹配的代码)")
        return "\n".join(lines)
