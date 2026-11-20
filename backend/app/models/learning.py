from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, UniqueConstraint

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
