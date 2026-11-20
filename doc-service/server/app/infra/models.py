from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    products: Mapped[list["UserProduct"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    grants: Mapped[list["Grant"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserProduct(Base):
    __tablename__ = "user_products"
    __table_args__ = (UniqueConstraint("username", "product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(ForeignKey("users.username"), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    user: Mapped[User] = relationship(back_populates="products")


class Grant(Base):
    __tablename__ = "grants"
    __table_args__ = (UniqueConstraint("username", "product", "kind", "resource_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(ForeignKey("users.username"), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # product_all | domain | module
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    perm: Mapped[str] = mapped_column(String(16), nullable=False, default="write")
    user: Mapped[User] = relationship(back_populates="grants")


class Session(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("users.username"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentToken(Base):
    __tablename__ = "agent_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("users.username"), nullable=False)
    label: Mapped[str] = mapped_column(String(80), default="pack")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Board(Base):
    __tablename__ = "boards"
    __table_args__ = (UniqueConstraint("product", "rel_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    rel_path: Mapped[str] = mapped_column(String(120), nullable=False)
    doc: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = (UniqueConstraint("product", "id"),)

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(80), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    nested_board: Mapped[str | None] = mapped_column(String(120), nullable=True)
    board_rel: Mapped[str] = mapped_column(String(120), nullable=False, default="board.json")
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="feature")
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    title_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sub_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    current_version: Mapped[str] = mapped_column(String(32), default="v1.0")


class ModuleVersion(Base):
    __tablename__ = "module_versions"
    __table_args__ = (UniqueConstraint("product", "module_id", "semver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    module_id: Mapped[str] = mapped_column(String(80), nullable=False)
    semver: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle_band: Mapped[str] = mapped_column(String(16), default="pre")
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    admin_protected: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("product", "module_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    module_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    body_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DocumentRevision(Base):
    __tablename__ = "document_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    module_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    body_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    board_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class DocumentView(Base):
    """Per-user view counts and last-seen for module, requirement, home, or draft."""

    __tablename__ = "document_views"
    __table_args__ = (UniqueConstraint("kind", "product", "resource_id", "version", "username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # module | requirement | home | draft
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisitLog(Base):
    __tablename__ = "visit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    product: Mapped[str | None] = mapped_column(String(8), nullable=True)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class ProductRelease(Base):
    __tablename__ = "product_releases"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    semver: Mapped[str] = mapped_column(String(32), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifecycle_band: Mapped[str] = mapped_column(String(16), default="dev")
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    added_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    fixed_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    remark_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class ReleaseCover(Base):
    __tablename__ = "release_covers"
    __table_args__ = (UniqueConstraint("release_id", "module_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("product_releases.id"), nullable=False)
    module_id: Mapped[str] = mapped_column(String(80), nullable=False)
    module_version: Mapped[str] = mapped_column(String(32), nullable=False)


class ProductTag(Base):
    __tablename__ = "product_tags"
    __table_args__ = (UniqueConstraint("product", "tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    tag: Mapped[str] = mapped_column(String(16), nullable=False)
    release_id: Mapped[str] = mapped_column(ForeignKey("product_releases.id"), nullable=False)


class ReleaseNote(Base):
    __tablename__ = "release_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("product_releases.id"), nullable=False)
    module_id: Mapped[str] = mapped_column(String(80), nullable=False)
    relative: Mapped[str] = mapped_column(String(80), default="")
    explanation_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class HourAllocation(Base):
    __tablename__ = "hour_allocations"
    __table_args__ = (UniqueConstraint("week", "username", "product", "feature_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week: Mapped[str] = mapped_column(String(16), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    feature_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    hours: Mapped[float] = mapped_column(Float, nullable=False)


class HourExpectation(Base):
    __tablename__ = "hour_expectations"
    __table_args__ = (UniqueConstraint("username", "product", "feature_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    feature_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_hours: Mapped[float] = mapped_column(Float, nullable=False)


class HoursWeekClose(Base):
    __tablename__ = "hours_week_closes"
    __table_args__ = (UniqueConstraint("week", "username", "product", "feature_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week: Mapped[str] = mapped_column(String(16), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    feature_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    hours: Mapped[float] = mapped_column(Float, nullable=False)


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    tag: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RequirementDoc(Base):
    __tablename__ = "requirement_docs"

    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), primary_key=True)
    body_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RequirementRevision(Base):
    __tablename__ = "requirement_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    body_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementComment(Base):
    __tablename__ = "requirement_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementAssignee(Base):
    __tablename__ = "requirement_assignees"
    __table_args__ = (UniqueConstraint("requirement_id", "username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)


class RequirementReviewRound(Base):
    __tablename__ = "requirement_review_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    started_by: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)


class RequirementReviewer(Base):
    __tablename__ = "requirement_reviewers"
    __table_args__ = (UniqueConstraint("round_id", "username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    round_id: Mapped[int] = mapped_column(ForeignKey("requirement_review_rounds.id"), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RequirementFeatureLink(Base):
    __tablename__ = "requirement_feature_links"
    __table_args__ = (UniqueConstraint("requirement_id", "feature_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    product: Mapped[str] = mapped_column(String(8), nullable=False)
    feature_id: Mapped[str] = mapped_column(String(80), nullable=False)


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    audience: Mapped[str] = mapped_column(String(16), nullable=False, default="listed", server_default="listed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DraftViewer(Base):
    __tablename__ = "draft_viewers"
    __table_args__ = (UniqueConstraint("draft_id", "username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)


class DraftComment(Base):
    __tablename__ = "draft_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    product: Mapped[str | None] = mapped_column(String(8), nullable=True)
    resource: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
