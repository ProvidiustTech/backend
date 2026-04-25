"""
app/models/cs.py
=================
ORM models for the Customer Service Agent.

Tables:
  company_registrations  — registered companies with their URL + scrape cache
  cs_sessions            — one per user↔company conversation
  cs_messages            — individual turns within a session (full history)
  escalations            — log of every escalation event for analytics
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.services.database import Base


class CompanyRegistration(Base):
    """
    A registered company. The CS agent scrapes this URL to build its knowledge base.
    scraped_context is cached here so we don't re-scrape on every conversation.
    """
    __tablename__ = "company_registrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Company identity
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str | None] = mapped_column(String(64))  # finance | healthcare | retail | ...
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    # Scraped knowledge — cached here, refreshed on demand
    scraped_context: Mapped[str] = mapped_column(Text, default="")
    pages_scraped: Mapped[list] = mapped_column(JSONB, default=list)  # list of URLs scraped
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scrape_status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|done|failed

    # Optional pgvector collection for uploaded docs (secondary source)
    collection_id: Mapped[str | None] = mapped_column(String(64))

    # Agent tuning
    agent_name: Mapped[str] = mapped_column(String(128), default="Support Agent")
    system_prompt_override: Mapped[str] = mapped_column(Text, default="")  # replace default system prompt
    escalation_email: Mapped[str | None] = mapped_column(String(320))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list["CSSession"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class CSSession(Base):
    """
    One customer service conversation session.
    A session = one customer talking to one company's agent.
    """
    __tablename__ = "cs_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company_registrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # end_user_id is the CUSTOMER — not the company owner
    end_user_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(32), default="active")  # active | escalated | resolved | closed
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    avg_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company: Mapped["CompanyRegistration"] = relationship(back_populates="sessions")
    messages: Mapped[list["CSMessage"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class CSMessage(Base):
    """A single message turn within a CS session."""
    __tablename__ = "cs_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cs_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Agent metadata (only on assistant messages)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    sources_used: Mapped[list] = mapped_column(JSONB, default=list)   # ["web_scrape", "rag", "resolved_cases"]
    hallucination_score: Mapped[float] = mapped_column(Float, default=0.0)
    should_escalate: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["CSSession"] = relationship(back_populates="messages")


class Escalation(Base):
    """Log of escalation events — useful for analytics and human agent routing."""
    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cs_sessions.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_message: Mapped[str] = mapped_column(Text, default="")  # the message that caused escalation
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String(256))  # human agent ID

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["CSSession"] = relationship(back_populates="escalations")
