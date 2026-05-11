"""
app/schemas/cs.py
==================
Pydantic v2 request/response schemas for the Customer Service Agent API.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


# ── Request schemas ───────────────────────────────────────────────────────────

class CompanyRegisterRequest(BaseModel):
    """Register a company so the CS agent can serve its customers."""
    company_name: str = Field(min_length=1, max_length=256)
    company_url: str = Field(description="Public URL — will be scraped for About, FAQ, pricing, etc.")
    industry: str | None = Field(
        default=None,
        description="finance | healthcare | retail | manufacturing | logistics | other",
    )
    team_size: str | None = Field(
        default=None,
        description="Range of employees, e.g., '1-10', '11-50'",
    )
    monthly_volume: str | None = Field(
        default=None,
        description="Expected monthly support tickets, e.g., '0-100', '100-500'",
    )
    channels: list[str] = Field(
        default_factory=list,
        description="List of selected support channels, e.g., ['web', 'whatsapp', 'email']",
    )
    training_data: str | None = Field(
        default=None,
        max_length=10000,
        description="Custom training text provided during onboarding to ground the agent.",
    )
    agent_name: str = Field(
        default="Support Agent",
        description="Name the AI agent uses when greeting customers",
    )
    escalation_email: str | None = Field(
        default=None,
        description="Email to notify when a conversation is escalated to a human",
    )
    system_prompt_override: str = Field(
        default="",
        max_length=2000,
        description="Replace the default system prompt. Leave blank to use built-in prompt.",
    )
    collection_id: str | None = Field(
        default=None,
        description="Optional pgvector collection ID for uploaded company documents (secondary source)",
    )


class CompanyRefreshRequest(BaseModel):
    """Trigger a re-scrape of the company URL."""
    force: bool = Field(default=False, description="Force re-scrape even if cache is fresh")


class CSChatRequest(BaseModel):
    """A customer message to the CS agent."""
    message: str = Field(min_length=1, max_length=4096, description="Customer's message")
    company_id: UUID = Field(description="Which company's CS agent to talk to")
    session_id: UUID | None = Field(
        default=None,
        description="Continue existing session. Omit to start a new one.",
    )
    # Optional: pre-populate end_user_id for identified users (e.g. from your auth system)
    end_user_id: str | None = Field(
        default=None,
        description="Customer identifier from your system (email, user ID, etc.)",
    )
    stream: bool = Field(default=True, description="Stream response via SSE")


class SaveResolvedCaseRequest(BaseModel):
    company_id: UUID
    problem: str = Field(min_length=5)
    resolution: str = Field(min_length=5)
    category: str | None = None


# ── Response schemas ──────────────────────────────────────────────────────────

class ScrapedPage(BaseModel):
    url: str
    page_type: str  # main | about | faq | pricing | contact
    char_count: int


class CompanyRegisterResponse(BaseModel):
    company_id: UUID
    company_name: str
    scrape_status: str  # pending | done | failed
    pages_scraped: list[ScrapedPage] = []
    context_chars: int = 0
    message: str


class CompanyDetailResponse(BaseModel):
    company_id: UUID
    company_name: str
    url: str
    industry: str | None
    agent_name: str
    scrape_status: str
    pages_scraped: list[str]
    context_chars: int
    last_scraped_at: datetime | None
    collection_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SourceUsed(BaseModel):
    type: str          # web_scrape | rag_document | resolved_case | llm_knowledge
    label: str         # human-readable description
    confidence: float


class CSChatResponse(BaseModel):
    """Non-streaming CS agent response."""
    answer: str
    session_id: UUID
    company_id: UUID

    # Transparency fields
    should_escalate: bool
    escalation_reason: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    sources_used: list[SourceUsed] = []
    hallucination_score: float = Field(ge=0.0, le=1.0, default=0.0)

    # Timing
    node_timings: dict[str, float] = {}


class SessionSummary(BaseModel):
    session_id: UUID
    status: str
    message_count: int
    escalated: bool
    started_at: datetime
    last_message_at: datetime

    model_config = {"from_attributes": True}


class MessageRecord(BaseModel):
    id: UUID
    role: str
    content: str
    confidence: float
    sources_used: list[str]
    should_escalate: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionDetailResponse(BaseModel):
    session: SessionSummary
    messages: list[MessageRecord]


class EscalationSummary(BaseModel):
    total: int
    unresolved: int
    escalations: list[dict]
