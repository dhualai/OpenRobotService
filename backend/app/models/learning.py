from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, UniqueConstraint

from app.models.base import Base


def new_id():
    return uuid4().hex


class LearningArticle(Base):
    __tablename__ = "learning_articles"

    id = Column(String(32), primary_key=True, default=new_id)
    title = Column(String(255), nullable=False)
    category = Column(String(80), nullable=False, default="未分类")
    draft = Column(JSON(none_as_null=True), nullable=True)
    sources = Column(JSON, nullable=False)
    active_version = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False, index=True)


class LearningArticleVersion(Base):
    __tablename__ = "learning_article_versions"
    __table_args__ = (UniqueConstraint("article_id", "version", name="uq_learning_article_version"),)

    id = Column(String(32), primary_key=True, default=new_id)
    article_id = Column(String(32), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    confirmed_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)


class LearningQuestion(Base):
    __tablename__ = "learning_questions"

    id = Column(String(32), primary_key=True, default=new_id)
    article_id = Column(String(32), nullable=False, index=True)
    article_version = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    review_status = Column(String(24), nullable=False, default="draft", index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class LearningGeneration(Base):
    __tablename__ = "learning_generations"

    id = Column(String(64), primary_key=True)
    article_id = Column(String(32), nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    input_revision = Column(Integer, nullable=False)
    article_version = Column(Integer, nullable=False)
    operator_id = Column(String(64), nullable=False)
    state = Column(String(24), nullable=False)
    result = Column(JSON, nullable=True)
    error = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class LearningAudit(Base):
    __tablename__ = "learning_audits"

    id = Column(String(32), primary_key=True, default=new_id)
    article_id = Column(String(32), nullable=False, index=True)
    question_id = Column(String(32), nullable=True)
    operator_id = Column(String(64), nullable=False)
    action = Column(String(40), nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False)


class LearningAnswer(Base):
    """做题人答题记录——允许多次提交，进度/答对率取最后一次结果。"""

    __tablename__ = "learning_answers"
    __table_args__ = (
        Index("ix_learning_answers_user_article", "user_id", "article_id"),
        Index("ix_learning_answers_user_article_q", "user_id", "article_id", "question_id", "article_version"),
    )

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(64), nullable=False, index=True)
    article_id = Column(String(32), nullable=False, index=True)
    article_version = Column(Integer, nullable=False)
    question_id = Column(String(32), nullable=False, index=True)
    answers = Column(JSON, nullable=False)  # 用户提交的选项 key 列表，如 ["A", "C"]
    is_correct = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=False)


class LearningWrongBook(Base):
    """错题本——用户曾经答错的题目，用于标记错题状态。

    同一用户对同一版本文章的同一道题只保留一条记录，
    首次答错时创建，后续再次答题时更新状态。
    """

    __tablename__ = "learning_wrongbook"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", "article_version", name="uq_learning_wrongbook_once"),
    )

    id = Column(String(32), primary_key=True, default=new_id)
    user_id = Column(String(64), nullable=False, index=True)
    article_id = Column(String(32), nullable=False, index=True)
    article_version = Column(Integer, nullable=False)
    question_id = Column(String(32), nullable=False, index=True)
    question_payload_snapshot = Column(JSON, nullable=False)  # 答错时的题目快照（题干/选项）
    # 错题状态：
    #   wrong    — 曾答错，当前未掌握
    #   mastered — 后来答对应掌握
    #   skipped  — 用户标记跳过（暂时放弃）
    status = Column(String(16), nullable=False, default="wrong", index=True)
    wrong_count = Column(Integer, nullable=False, default=1)  # 累计答错次数
    mastered_at = Column(DateTime, nullable=True)              # 变为已掌握的时间
    last_wrong_at = Column(DateTime, nullable=False)           # 最近一次答错的时间
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
