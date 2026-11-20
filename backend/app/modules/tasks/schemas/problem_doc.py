"""工单「问题文档」AI 生成 schema。

- ProblemDocSourceItem：一条发言（会话消息或讨论区评论，二者字段取并集）
- ProblemDocGenerateRequest：生成入参（只吃文本，不依赖 task_id —— 提单阶段工单还不存在）
- ProblemDocGenerateResponse：生成结果（不落库，写入由前端二次确认后调 spec-doc）
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class ProblemDocSourceItem(BaseModel):
    """一条发言。

    会话场景填 role（user / assistant）+ content；讨论区场景填 author + content。
    created_at 原样透传给提示词做时间标注，不参与任何解析。
    """
    role: Optional[str] = Field(None, description="会话角色：user / assistant")
    author: Optional[str] = Field(None, description="发言人姓名（讨论区场景）")
    content: str = Field("", description="发言正文")
    created_at: Optional[str] = Field(None, description="发言时间（仅用于提示词标注）")


class ProblemDocGenerateRequest(BaseModel):
    items: List[ProblemDocSourceItem] = Field(
        default_factory=list, max_length=200, description="待整理的发言（按时间正序）"
    )
    project_name: Optional[str] = Field(None, description="项目名（提示词里给模型一点背景）")
    scene: str = Field("conversation", description="来源场景：conversation / discussion")


class ProblemDocGenerateResponse(BaseModel):
    markdown: str = Field(..., description="生成的补充段正文（markdown）")
    model: Optional[str] = Field(None, description="实际使用的模型名")
    used: int = Field(0, description="实际整理的发言条数")
    dropped: int = Field(0, description="因超上限被省略的发言条数")
    truncated: bool = Field(False, description="正文是否被截断")
