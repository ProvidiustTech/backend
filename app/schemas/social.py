"""
app/schemas/social.py
======================
Pydantic v2 request/response schemas for all Social Media Agent endpoints.
These are the API contracts — what clients send and what they receive.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


# ── Request schemas ───────────────────────────────────────────────────────────

class SocialComposeRequest(BaseModel):
    """Full pipeline: profile analysis → trends → compose → refine."""

    social_links: list[str] = Field(
        default_factory=list,
        description="Public profile URLs (LinkedIn, Twitter/X, Instagram, etc.)",
        examples=[["https://linkedin.com/in/yourusername"]],
    )
    niche: str | None = Field(
        default=None, max_length=120,
        description="Your niche — auto-detected from profiles if omitted",
        examples=["fintech startup founder"],
    )
    platform: str = Field(
        default="linkedin",
        pattern="^(twitter|linkedin|instagram|threads|facebook)$",
    )
    tone: str = Field(
        default="professional",
        pattern="^(professional|casual|witty|inspirational|educational)$",
    )
    custom_instructions: str = Field(
        default="", max_length=500,
        description="Extra instructions: topic angle, CTA, language, audience focus",
        examples=["Focus on the Nigerian market. End with a question about local adoption."],
    )
    save_to_history: bool = Field(
        default=True,
        description="Persist the generated post to post history",
    )


class TrendsRequest(BaseModel):
    niche: str = Field(min_length=2, max_length=120, examples=["fintech", "AI in healthcare"])
    limit: int = Field(default=5, ge=1, le=15)


class ProfileAnalyseRequest(BaseModel):
    social_links: list[str] = Field(min_length=1)
    save_profile: bool = Field(
        default=True,
        description="Persist the profile analysis for future requests",
    )


class RefinePostRequest(BaseModel):
    post: str = Field(min_length=10, max_length=5000)
    platform: str = Field(default="linkedin", pattern="^(twitter|linkedin|instagram|threads|facebook)$")
    feedback: str = Field(default="", max_length=500)
    post_id: UUID | None = Field(
        default=None,
        description="If provided, updates the existing post record in history",
    )


class PostStatusUpdate(BaseModel):
    status: str = Field(pattern="^(draft|approved|published|archived)$")


class SchedulePostRequest(BaseModel):
    post_id: UUID
    platform: str = Field(pattern="^(twitter|linkedin|instagram|threads|facebook)$")
    scheduled_for: datetime = Field(description="UTC datetime to publish")


# ── Response schemas ──────────────────────────────────────────────────────────

class TrendItem(BaseModel):
    title: str
    summary: str
    url: str = ""
    published: str = ""
    relevance_score: float = 0.0
    source: str = ""


class UserProfile(BaseModel):
    niche: str
    style: str
    audience: str
    topics: list[str] = []
    voice_samples: list[str] = []
    content_gaps: list[str] = []
    confidence: str = "medium"


class SocialComposeResponse(BaseModel):
    """Full response from the compose pipeline."""

    # The main deliverable
    post: str
    platform: str
    hashtags: list[str]
    image_prompt: str
    quality_score: float = Field(ge=0.0, le=1.0)
    quality_feedback: str

    # A/B testing variants
    variants: list[str] = []

    # Trend context
    selected_trend: dict[str, Any] = {}
    top_trends: list[TrendItem] = []

    # Creator profile (useful for frontend onboarding display)
    detected_niche: str
    user_profile: UserProfile | None = None

    # Persistence
    post_id: UUID | None = None  # set if save_to_history=True


class TrendsResponse(BaseModel):
    niche: str
    count: int
    trends: list[TrendItem]
    cache_ttl_minutes: int = 60


class ProfileAnalyseResponse(BaseModel):
    links_processed: int
    platforms_found: list[str]
    detected_niche: str
    profile: UserProfile
    profile_id: UUID | None = None  # set if save_profile=True


class RefinePostResponse(BaseModel):
    original: str
    refined: str
    quality_score: float
    feedback: str
    platform: str
    post_id: UUID | None = None


class PostHistoryItem(BaseModel):
    id: UUID
    platform: str
    post_text: str
    hashtags: list[str]
    niche: str
    trend_title: str
    quality_score: float
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PostHistoryResponse(BaseModel):
    total: int
    posts: list[PostHistoryItem]


class PlatformInfo(BaseModel):
    char_limit: int
    style: str
    hook_style: str


class PlatformsResponse(BaseModel):
    platforms: dict[str, PlatformInfo]
