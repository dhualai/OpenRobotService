from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT

from app.models.base import Base


class KnowledgePublication(Base):
    __tablename__ = "knowledge_publications"

    task_id = Column(BigInteger, primary_key=True)
    report_id = Column(BigInteger, nullable=False)
    questioner_id = Column(String(64), nullable=False, index=True)
    answerer_id = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    problem = Column(Text, nullable=False)
    content = Column(Text().with_variant(LONGTEXT, "mysql"), nullable=False)
    review_comment = Column(Text, nullable=False)
    value_point = Column(Text, nullable=False)
    first_approved_at = Column(DateTime, nullable=False, index=True)
    active = Column(Boolean, nullable=False, default=True)


class KnowledgeLike(Base):
    __tablename__ = "knowledge_question_likes"

    task_id = Column(BigInteger, primary_key=True)
    user_id = Column(String(64), primary_key=True)
    active = Column(Boolean, nullable=False, default=True)
    risk_status = Column(String(20), nullable=False, default="valid")
    risk_note = Column(Text, nullable=True)
    risk_operator = Column(String(64), nullable=True)
    updated_at = Column(DateTime, nullable=False)


class KnowledgeLikeEvent(Base):
    __tablename__ = "knowledge_like_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(String(64), nullable=False)
    active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True)


class ArchiveReviewDecision(Base):
    __tablename__ = "archive_review_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, nullable=False, index=True)
    report_id = Column(BigInteger, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    reviewer_id = Column(String(64), nullable=False, index=True)
    outcome = Column(String(20), nullable=False)
    review_comment = Column(Text, nullable=False)
    value_point = Column(Text, nullable=True)
    content = Column(Text().with_variant(LONGTEXT, "mysql"), nullable=False)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("report_id", "version", name="uq_archive_review_version"),)


class KnowledgeSettlement(Base):
    __tablename__ = "knowledge_settlements"

    month = Column(String(7), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    status = Column(String(24), nullable=False, default="reviewing")
    rankings = Column(JSON, nullable=False)
    audit = Column(JSON, nullable=False, default=list)
    frozen_at = Column(DateTime, nullable=False)
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    granted_at = Column(DateTime, nullable=True)


class KnowledgeDiscussion(Base):
    __tablename__ = "knowledge_discussions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, nullable=False, index=True)
    author_id = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True)
