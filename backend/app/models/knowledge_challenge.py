from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint

from app.models.base import Base


class KnowledgeChallenge(Base):
    __tablename__ = "knowledge_challenges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, nullable=True, unique=True)
    request_key = Column(String(64), nullable=False, unique=True)
    creation_fingerprint = Column(String(64), nullable=False)
    publisher_id = Column(String(64), nullable=False)
    questioner_id = Column(String(64), nullable=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    bounty_fen = Column(Integer, nullable=False)
    ends_at = Column(DateTime, nullable=False, index=True)
    first_published_at = Column(DateTime, nullable=True)
    asked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)
    state = Column(String(24), nullable=False, default="draft", index=True)
    hidden = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    rule_version = Column(String(32), nullable=False, default="likes-first-submit-v1")
    default_answer_id = Column(Integer, nullable=True)
    best_answer_id = Column(Integer, nullable=True)
    settled_at = Column(DateTime, nullable=True)
    snapshot = Column(JSON, nullable=True)
    reward_attention = Column(Boolean, nullable=False, default=False)
    last_error = Column(Text, nullable=True)


class KnowledgeChallengeAnswer(Base):
    __tablename__ = "knowledge_challenge_answers"
    __table_args__ = (UniqueConstraint("challenge_id", "author_id", name="uq_challenge_answer_author"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    challenge_id = Column(Integer, nullable=False, index=True)
    author_id = Column(String(64), nullable=False)
    content = Column(Text, nullable=False, default="")
    draft_content = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    hidden = Column(Boolean, nullable=False, default=False)
    history = Column(JSON, nullable=False, default=list)


class KnowledgeChallengeLike(Base):
    __tablename__ = "knowledge_challenge_likes"

    target_kind = Column(String(16), primary_key=True)
    target_id = Column(Integer, primary_key=True)
    user_id = Column(String(64), primary_key=True)
    active = Column(Boolean, nullable=False, default=True)


class KnowledgeChallengeLikeEvent(Base):
    __tablename__ = "knowledge_challenge_like_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_kind = Column(String(16), nullable=False)
    target_id = Column(Integer, nullable=False)
    user_id = Column(String(64), nullable=False, index=True)
    active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True)


class KnowledgeChallengeAudit(Base):
    __tablename__ = "knowledge_challenge_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    challenge_id = Column(Integer, nullable=False, index=True)
    operator_id = Column(String(64), nullable=False)
    action = Column(String(32), nullable=False)
    reason = Column(Text, nullable=False)
    before = Column(JSON, nullable=False)
    after = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False)
