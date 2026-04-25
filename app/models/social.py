"""
app/models/social.py
=====================
ORM models for the Social Media Agent feature set.

Tables:
  - social_profiles   : stored creator profiles (niche, style, links)
  - social_posts      : generated post history per user
  - social_schedules  : scheduled posts (future feature)
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.services.database import Base


class SocialProfile(Base):
    """
    Persisted creator profile — built from scraping social links.
    Cached so repeat requests don't re-scrape.
    Updated whenever the user calls /social/profile again.
    """
    __tablename__ = "social_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Detected profile data
    detected_niche: Mapped[str] = mapped_column(String(200), default="")
    writing_style: Mapped[str] = mapped_column(String(64), default="professional")
    target_audience: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[list] = mapped_column(JSONB, default=list)          # list of topic strings
    voice_samples: Mapped[list] = mapped_column(JSONB, default=list)   # characteristic phrases
    content_gaps: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[str] = mapped_column(String(16), default="low")  # high | medium | low

    # Source links that built this profile
    social_links: Mapped[list] = mapped_column(JSONB, default=list)
    raw_profiles: Mapped[list] = mapped_column(JSONB, default=list)    # raw scrape data

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    posts: Mapped[list["SocialPost"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class SocialPost(Base):
    """
    A generated social media post — stored for history, analytics, and re-use.
    """
    __tablename__ = "social_posts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_profiles.id", ondelete="SET NULL"), nullable=True
    )

    # Content
    platform: Mapped[str] = mapped_column(String(32), nullable=False)     # twitter | linkedin | ...
    post_text: Mapped[str] = mapped_column(Text, nullable=False)           # final refined post
    draft_text: Mapped[str] = mapped_column(Text, default="")             # pre-refinement
    hashtags: Mapped[list] = mapped_column(JSONB, default=list)
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    variants: Mapped[list] = mapped_column(JSONB, default=list)            # 3 angle variants

    # Context that produced this post
    niche: Mapped[str] = mapped_column(String(200), default="")
    trend_title: Mapped[str] = mapped_column(String(500), default="")     # trend it was based on
    trend_url: Mapped[str] = mapped_column(Text, default="")
    custom_instructions: Mapped[str] = mapped_column(Text, default="")

    # Quality
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_feedback: Mapped[str] = mapped_column(Text, default="")

    # Status
    status: Mapped[str] = mapped_column(String(32), default="draft")      # draft | approved | published | archived
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profile: Mapped["SocialProfile | None"] = relationship(back_populates="posts")


class SocialSchedule(Base):
    """
    Scheduled post — placeholder for the scheduling feature (Phase 2).
    """
    __tablename__ = "social_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_posts.id", ondelete="CASCADE")
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")    # pending | sent | failed | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
