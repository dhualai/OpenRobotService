"""知识补充（ORS-1159）：待补充知识点 + 多人答案草稿。

数据链：
  dar_weekly supplement 步骤把漏斗「未覆盖」段的用户提问归纳成通用知识点
  → 工作台「知识补充」审核台人工过一遍 → 推送本表（upsert，id=标题 hash 跨周稳定）
  → 服务号「知识库 → 待补充」：部门多人提交答案草稿（互相可见）
  → AI 汇总成标准答案（is_merged 草稿）→ 提交审核 → 审核人通过
  → 写 kb/team/chat_qa/{标题}.md（H1 + 正文）→ 下次周入库生效。

状态机：
  open（待补充）→ drafting（已有草稿）→ merged（AI 已汇总）
  → reviewing（提交审核）→ approved（通过入库）/ rejected（驳回，回 merged）
"""
from sqlalchemy import (Boolean, Column, DateTime, Integer, String, Text,
                        UniqueConstraint, func)

from app.models.base import Base


class KbSupplementQuestion(Base):
    __tablename__ = "kb_supplement_questions"

    id = Column(String(64), primary_key=True, comment="知识点ID（标题 hash，跨周稳定）")
    week_date = Column(String(8), nullable=False, index=True, comment="最近一次推送批次的周日期")
    env = Column(String(16), nullable=False, default="prod", comment="数据环境 prod/test")
    title = Column(String(255), nullable=False, comment="通用问题标题（未来入库 H1/检索入口）")
    description = Column(Text, nullable=True, comment="知识点要回答什么（现象/条件/影响）")
    qtype = Column(String(32), nullable=False, default="其他", comment="故障处置/操作指引/…")
    hit_count = Column(Integer, nullable=False, default=0, comment="累计出现次数（推送批次累加）")
    status = Column(String(24), nullable=False, default="open", index=True,
                    comment="open/drafting/merged/reviewing/approved/rejected")
    pushed_at = Column(DateTime, nullable=True, comment="首次推送时间")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(),
                        nullable=False)

    __table_args__ = (
        UniqueConstraint("id", name="uq_kb_supplement_question_id"),
    )


class KbSupplementDraft(Base):
    __tablename__ = "kb_supplement_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(String(64), nullable=False, index=True, comment="知识点ID")
    author_id = Column(String(64), nullable=False, index=True, comment="作者 users.id")
    author_name = Column(String(128), nullable=True, comment="作者显示名（快照）")
    content_md = Column(Text, nullable=False, default="", comment="答案草稿 markdown")
    media = Column(Text, nullable=True, comment="图片引用 JSON（[{path, url}]）")
    is_merged = Column(Boolean, nullable=False, default=False,
                       comment="AI 汇总稿（一个知识点最多一条，重新汇总覆盖）")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(),
                        nullable=False)

    __table_args__ = (
        # 每人每个知识点只有一条草稿（重复提交覆盖）；汇总稿用 author_id='__ai__'
        UniqueConstraint("question_id", "author_id", name="uq_kb_supp_draft_author"),
    )
