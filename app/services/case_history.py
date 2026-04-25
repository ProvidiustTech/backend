"""
app/services/case_history.py
=============================
Stores and retrieves resolved customer service cases.
Used by the Customer Service Agent for similar-case retrieval.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.logging import get_logger
from app.services.database import Base, get_db_context

log = get_logger(__name__)


class ResolvedCase(Base):
    """A resolved customer service case stored for future reference."""
    __tablename__ = "resolved_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    resolved_by: Mapped[str] = mapped_column(String(32), default="ai")  # ai | human
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def save_resolved_case(
    company_id: str,
    problem: str,
    resolution: str,
    category: str | None = None,
    resolved_by: str = "ai",
) -> str:
    """Save a resolved case to the database for future retrieval."""
    async with get_db_context() as db:
        case = ResolvedCase(
            company_id=company_id,
            problem=problem,
            resolution=resolution,
            category=category,
            resolved_by=resolved_by,
        )
        db.add(case)
        await db.flush()
        case_id = str(case.id)
    log.info("resolved case saved", company_id=company_id, case_id=case_id)
    return case_id


async def find_similar_cases(
    query: str,
    company_id: str,
    limit: int = 3,
) -> list[dict]:
    """
    Find similar past cases using keyword matching.
    In production, replace with vector similarity search.
    """
    async with get_db_context() as db:
        result = await db.execute(
            select(ResolvedCase)
            .where(ResolvedCase.company_id == company_id)
            .order_by(ResolvedCase.created_at.desc())
            .limit(20)
        )
        cases = result.scalars().all()

    if not cases:
        return []

    # Simple keyword overlap scoring
    query_words = set(query.lower().split())
    scored = []
    for case in cases:
        case_words = set(case.problem.lower().split())
        overlap = len(query_words & case_words) / max(len(query_words), 1)
        if overlap > 0.2:
            scored.append((overlap, case))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        {
            "id": str(c.id),
            "problem": c.problem,
            "resolution": c.resolution,
            "category": c.category,
            "resolved_by": c.resolved_by,
            "score": round(score, 3),
        }
        for score, c in scored[:limit]
    ]
