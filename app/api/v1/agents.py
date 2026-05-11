"""
app/api/v1/agents.py
=====================
Agent API — Customer Service Agent is PRIMARY, Social Media Agent is SECONDARY.

Customer Service (primary):
  POST /api/v1/agents/cs/register           Register company + trigger scrape
  POST /api/v1/agents/cs/register/{id}/refresh   Re-scrape company URL
  GET  /api/v1/agents/cs/companies          List registered companies
  GET  /api/v1/agents/cs/companies/{id}     Company detail + scrape status
  POST /api/v1/agents/cs/chat               Chat — JSON response
  POST /api/v1/agents/cs/chat/stream        Chat — SSE stream with node progress
  GET  /api/v1/agents/cs/sessions           List sessions for a company
  GET  /api/v1/agents/cs/sessions/{id}      Session detail + full message history
  POST /api/v1/agents/cs/cases              Save a resolved case
  GET  /api/v1/agents/cs/escalations        List escalations for a company

Social Media (secondary):
  POST /api/v1/agents/social/compose        Full pipeline JSON
  POST /api/v1/agents/social/compose/stream Full pipeline SSE
  POST /api/v1/agents/social/trends         Trends only
  POST /api/v1/agents/social/profile        Profile analysis only
  POST /api/v1/agents/social/refine         Refine a draft
  GET  /api/v1/agents/social/history        Post history
  PATCH /api/v1/agents/social/posts/{id}    Update post status
  GET  /api/v1/agents/social/platforms      Platform rules

Health:
  GET  /api/v1/agents/health
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, AsyncGenerator, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status, Form, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_user
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.cs import (
    CompanyRegisterRequest,
    CompanyRegisterResponse,
    CompanyDetailResponse,
    CSChatRequest,
    CSChatResponse,
    SaveResolvedCaseRequest,
    ScrapedPage,
    SourceUsed,
    EscalationSummary,
    SessionSummary,
    MessageRecord,
    SessionDetailResponse,
)
from app.schemas.social import (
    PostHistoryItem,
    PostHistoryResponse,
    PostStatusUpdate,
    PlatformInfo,
    PlatformsResponse,
    ProfileAnalyseRequest,
    ProfileAnalyseResponse,
    RefinePostRequest,
    RefinePostResponse,
    SocialComposeRequest,
    SocialComposeResponse,
    TrendItem,
    TrendsRequest,
    TrendsResponse,
    UserProfile,
)
from app.services.database import get_db

log = get_logger(__name__)
router = APIRouter(prefix="/agents", tags=["AI Agents"])
onboarding_router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


# ══════════════════════════════════════════════════════════════════════════════
# CS AGENT — COMPANY REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/cs/register",
    response_model=CompanyRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a company and trigger URL scraping",
)
async def cs_register(
    request: CompanyRegisterRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> CompanyRegisterResponse:
    """
    Register a company for the CS agent.

    What happens:
    1. Creates a `company_registrations` record in the database
    2. Immediately triggers a background scrape of the company URL
    3. Returns the company_id — use this in all /cs/chat requests
    4. The scrape runs asynchronously — check `/cs/companies/{id}` for status

    The scrape discovers and fetches:
    - Main page (homepage)
    - About / About Us page
    - FAQ / Help / Support page
    - Pricing page
    - Contact page
    - Sitemap (for additional page discovery)

    All scraped content is cached in the database. Subsequent conversations
    use the cached content and do NOT re-scrape (fast).
    To force a fresh scrape: `POST /cs/register/{id}/refresh`
    """
    from app.models.cs import CompanyRegistration

    company = CompanyRegistration(
        owner_id=current_user.id,
        name=request.company_name,
        url=request.company_url,
        industry=request.industry,
        team_size=request.team_size,
        monthly_volume=request.monthly_volume,
        channels=request.channels,
        training_data=request.training_data,
        agent_name=request.agent_name,
        escalation_email=request.escalation_email,
        system_prompt_override=request.system_prompt_override,
        collection_id=request.collection_id,
        scrape_status="pending",
    )
    db.add(company)
    await db.flush()
    company_id = company.id
    await db.commit()

    log.info("company registered", company_id=str(company_id), name=request.company_name)

    # Trigger background scrape
    background_tasks.add_task(_background_scrape, str(company_id), request.company_url)

    return CompanyRegisterResponse(
        company_id=company_id,
        company_name=request.company_name,
        scrape_status="pending",
        pages_scraped=[],
        context_chars=0,
        message=(
            f"Company registered. Scraping {request.company_url} in the background. "
            f"Check GET /api/v1/agents/cs/companies/{company_id} for scrape status."
        ),
    )


@onboarding_router.post(
    "/setup",
    response_model=CompanyRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Onboarding setup alias for company registration",
)
async def onboarding_setup(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    industry: str = Form(""),
    teamSize: str = Form(""),
    monthlyVolume: str = Form(""),
    channels: str = Form("[]"),
    urls: str = Form("[]"),
    files: List[UploadFile] = File(None),
) -> CompanyRegisterResponse:
    """
    Alias for cs_register to match frontend onboarding flow.
    Handles Industry, Team Size, Monthly Volume, and Channels.
    """
    # Parse JSON strings from form fields
    try:
        channels_list = json.loads(channels)
        urls_list = json.loads(urls)
    except Exception:
        channels_list = []
        urls_list = []

    # Use first URL as company URL if available, otherwise fallback
    company_url = urls_list[0] if urls_list else "https://providius.ai/pending-setup"

    from app.models.cs import CompanyRegistration
    
    company = CompanyRegistration(
        owner_id=current_user.id,
        name=current_user.full_name, # Use user's company name from registration
        url=company_url,
        industry=industry,
        team_size=teamSize,
        monthly_volume=monthlyVolume,
        channels=channels_list,
        training_data=f"Imported URLs: {', '.join(urls_list)}",
        scrape_status="pending",
    )
    db.add(company)
    await db.flush()
    company_id = company.id
    await db.commit()

    log.info("onboarding setup complete", company_id=str(company_id))

    if urls_list:
        background_tasks.add_task(_background_scrape, str(company_id), company_url)

    return CompanyRegisterResponse(
        company_id=company_id,
        company_name=company.name,
        scrape_status="pending",
        pages_scraped=[],
        context_chars=0,
        message="Onboarding setup successful.",
    )


async def _background_scrape(company_id: str, url: str) -> None:
    """Background task: scrape company URL and persist result to DB."""
    from app.services.web_scraper import scrape_company_context
    from app.services.database import get_db_context
    from app.models.cs import CompanyRegistration

    log.info("background scrape starting", company_id=company_id, url=url[:80])

    try:
        combined, results = await scrape_company_context(
            url=url,
            company_id=company_id,
            force_refresh=True,
        )

        async with get_db_context() as db:
            res = await db.execute(
                select(CompanyRegistration).where(CompanyRegistration.id == company_id)
            )
            company = res.scalar_one_or_none()
            if company:
                company.scraped_context = combined
                company.pages_scraped = [
                    {"url": r.url, "page_type": r.page_type, "chars": r.char_count}
                    for r in results
                ]
                company.last_scraped_at = datetime.now(timezone.utc)
                company.scrape_status = "done" if combined else "failed"

        log.info(
            "background scrape complete",
            company_id=company_id,
            pages=len(results),
            chars=len(combined),
        )

    except Exception as e:
        log.exception("background scrape failed", company_id=company_id, error=str(e))
        try:
            from app.services.database import get_db_context
            from app.models.cs import CompanyRegistration
            async with get_db_context() as db:
                res = await db.execute(
                    select(CompanyRegistration).where(CompanyRegistration.id == company_id)
                )
                company = res.scalar_one_or_none()
                if company:
                    company.scrape_status = "failed"
        except Exception:
            pass


@router.post(
    "/cs/companies/{company_id}/refresh",
    summary="Force re-scrape of company URL",
)
async def cs_refresh_scrape(
    company_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Force a fresh scrape of the company URL (bypasses 24h cache)."""
    from app.models.cs import CompanyRegistration

    res = await db.execute(
        select(CompanyRegistration).where(
            CompanyRegistration.id == company_id,
            CompanyRegistration.owner_id == current_user.id,
        )
    )
    company = res.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.scrape_status = "pending"
    await db.commit()

    background_tasks.add_task(_background_scrape, str(company_id), company.url)
    return {"company_id": str(company_id), "status": "pending", "message": "Re-scrape triggered"}


@router.get(
    "/cs/companies",
    summary="List registered companies",
)
async def cs_list_companies(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """List all companies registered by the current user."""
    from app.models.cs import CompanyRegistration

    res = await db.execute(
        select(CompanyRegistration)
        .where(CompanyRegistration.owner_id == current_user.id)
        .order_by(CompanyRegistration.created_at.desc())
    )
    companies = res.scalars().all()

    return {
        "total": len(companies),
        "companies": [
            {
                "company_id": str(c.id),
                "name": c.name,
                "url": c.url,
                "industry": c.industry,
                "scrape_status": c.scrape_status,
                "pages_scraped": len(c.pages_scraped or []),
                "context_chars": len(c.scraped_context or ""),
                "last_scraped_at": c.last_scraped_at,
                "created_at": c.created_at,
            }
            for c in companies
        ],
    }


@router.get(
    "/cs/companies/{company_id}",
    summary="Get company detail and scrape status",
)
async def cs_get_company(
    company_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.models.cs import CompanyRegistration

    res = await db.execute(
        select(CompanyRegistration).where(
            CompanyRegistration.id == company_id,
            CompanyRegistration.owner_id == current_user.id,
        )
    )
    company = res.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    return {
        "company_id": str(company.id),
        "name": company.name,
        "url": company.url,
        "industry": company.industry,
        "agent_name": company.agent_name,
        "scrape_status": company.scrape_status,
        "pages_scraped": company.pages_scraped or [],
        "context_chars": len(company.scraped_context or ""),
        "last_scraped_at": company.last_scraped_at,
        "collection_id": company.collection_id,
        "created_at": company.created_at,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CS AGENT — CHAT (JSON)
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/cs/chat",
    response_model=CSChatResponse,
    summary="Chat with CS agent — JSON response",
)
async def cs_chat(
    request: CSChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> CSChatResponse:
    """
    Send a customer message and receive a grounded CS agent response.

    The agent:
    1. Loads cached company knowledge (web scrape of About/FAQ/Pricing)
    2. Finds similar resolved cases
    3. Searches uploaded documents if a collection is configured
    4. Generates a grounded answer with multi-turn memory
    5. Checks for escalation triggers

    All turns are persisted to `cs_sessions` and `cs_messages` for analytics.
    Use `stream=true` (POST /cs/chat/stream) for real-time token streaming.
    """
    from app.agents.customer_service import cs_agent, CSAgentState
    from app.models.cs import CompanyRegistration, CSSession, CSMessage

    # Load company
    comp_res = await db.execute(
        select(CompanyRegistration).where(CompanyRegistration.id == request.company_id)
    )
    company = comp_res.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found. Register it first via POST /cs/register")

    # Load or create session
    session_id = request.session_id
    chat_history: list[dict] = []

    if session_id:
        sess_res = await db.execute(select(CSSession).where(CSSession.id == session_id))
        session = sess_res.scalar_one_or_none()
        if session:
            # Load existing message history for this session
            msg_res = await db.execute(
                select(CSMessage).where(CSMessage.session_id == session_id)
                .order_by(CSMessage.created_at.asc())
            )
            for msg in msg_res.scalars().all():
                chat_history.append({"role": msg.role, "content": msg.content})
    else:
        end_user = request.end_user_id or f"user_{current_user.id}"
        session = CSSession(
            company_id=company.id,
            end_user_id=end_user,
        )
        db.add(session)
        await db.flush()
        session_id = session.id

    # Build initial state
    initial: CSAgentState = {
        "message": request.message,
        "company_id": str(company.id),
        "company_name": company.name,
        "company_url": company.url,
        "company_collection_id": company.collection_id,
        "agent_name": company.agent_name,
        "system_prompt_override": company.system_prompt_override or "",
        "industry": company.industry,
        "session_id": str(session_id),
        "end_user_id": request.end_user_id or str(current_user.id),
        "chat_history": chat_history,
        "messages": [],
        # Pre-populate from DB cache
        "scraped_context": company.scraped_context or "",
        "training_data": company.training_data or "",
        "scraped_pages": company.pages_scraped or [],
        "resolved_cases": [],
        "rag_chunks": [],
        "assembled_context": "",
        "sources_used": [],
        "answer": "",
        "confidence": 0.0,
        "should_escalate": False,
        "escalation_reason": None,
        "hallucination_score": 0.0,
        "node_timings": {},
    }

    try:
        final = await cs_agent.ainvoke(initial)
    except Exception as e:
        log.exception("CS agent failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

    # Persist messages
    db.add(CSMessage(
        session_id=session_id,
        role="user",
        content=request.message,
    ))
    db.add(CSMessage(
        session_id=session_id,
        role="assistant",
        content=final.get("answer", ""),
        confidence=final.get("confidence", 0.0),
        sources_used=final.get("sources_used", []),
        should_escalate=final.get("should_escalate", False),
    ))

    # Update session
    if session:
        session.message_count = (session.message_count or 0) + 2
        if final.get("should_escalate"):
            session.escalated = True
            session.status = "escalated"
            session.escalation_reason = final.get("escalation_reason")

    # Build sources_used response
    source_map = {
        "web_scrape": SourceUsed(type="web_scrape", label=f"Company website ({company.url})", confidence=0.9),
        "resolved_cases": SourceUsed(type="resolved_cases", label="Resolved support cases", confidence=0.85),
        "rag_documents": SourceUsed(type="rag_documents", label="Uploaded company documents", confidence=0.8),
    }
    sources = [source_map[s] for s in final.get("sources_used", []) if s in source_map]

    return CSChatResponse(
        answer=final.get("answer", ""),
        session_id=session_id,
        company_id=request.company_id,
        should_escalate=final.get("should_escalate", False),
        escalation_reason=final.get("escalation_reason"),
        confidence=final.get("confidence", 0.0),
        sources_used=sources,
        hallucination_score=final.get("hallucination_score", 0.0),
        node_timings=final.get("node_timings", {}),
    )


# ══════════════════════════════════════════════════════════════════════════════
# CS AGENT — CHAT STREAM (SSE)
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/cs/chat/stream",
    summary="Chat with CS agent — SSE streaming with node progress",
)
async def cs_chat_stream(
    request: CSChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """
    Same as /cs/chat but streams progress events via Server-Sent Events.

    Events emitted in order:
    - `{"type": "status", "node": "load_company_context", "message": "Loading company knowledge..."}`
    - `{"type": "sources", "sources_found": ["web_scrape", "resolved_cases"]}`
    - `{"type": "status", "node": "generate_response", "message": "Generating answer..."}`
    - `{"type": "token",  "content": "Hello "}` — streamed word by word
    - `{"type": "done",   "session_id": "...", "should_escalate": false, "confidence": 0.87}`
    - `{"type": "error",  "error": "..."}` — on failure
    """
    return StreamingResponse(
        _stream_cs_chat(request, current_user, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _stream_cs_chat(
    request: CSChatRequest,
    current_user: User,
    db: AsyncSession,
) -> AsyncGenerator[str, None]:
    from app.agents.customer_service import (
        CSAgentState,
        load_company_context_node,
        retrieve_cases_node,
        retrieve_rag_node,
        build_context_node,
        generate_response_node,
        check_escalation_node,
        escalate_node,
    )
    from app.models.cs import CompanyRegistration, CSSession, CSMessage

    def sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    # Load company
    try:
        comp_res = await db.execute(
            select(CompanyRegistration).where(CompanyRegistration.id == request.company_id)
        )
        company = comp_res.scalar_one_or_none()
        if not company:
            yield sse({"type": "error", "error": "Company not found. Register via POST /cs/register"})
            return
    except Exception as e:
        yield sse({"type": "error", "error": str(e)})
        return

    # Session handling
    session_id = request.session_id
    chat_history: list[dict] = []
    session = None

    if session_id:
        sess_res = await db.execute(select(CSSession).where(CSSession.id == session_id))
        session = sess_res.scalar_one_or_none()
        if session:
            msg_res = await db.execute(
                select(CSMessage).where(CSMessage.session_id == session_id)
                .order_by(CSMessage.created_at.asc())
            )
            for msg in msg_res.scalars().all():
                chat_history.append({"role": msg.role, "content": msg.content})

    if not session:
        session = CSSession(
            company_id=company.id,
            end_user_id=request.end_user_id or str(current_user.id),
        )
        db.add(session)
        await db.flush()
        session_id = session.id

    state: CSAgentState = {
        "message": request.message,
        "company_id": str(company.id),
        "company_name": company.name,
        "company_url": company.url,
        "company_collection_id": company.collection_id,
        "agent_name": company.agent_name,
        "system_prompt_override": company.system_prompt_override or "",
        "industry": company.industry,
        "session_id": str(session_id),
        "end_user_id": request.end_user_id or str(current_user.id),
        "chat_history": chat_history,
        "messages": [],
        "scraped_context": company.scraped_context or "",
        "training_data": company.training_data or "",
        "scraped_pages": company.pages_scraped or [],
        "resolved_cases": [],
        "rag_chunks": [],
        "assembled_context": "",
        "sources_used": [],
        "answer": "",
        "confidence": 0.0,
        "should_escalate": False,
        "escalation_reason": None,
        "hallucination_score": 0.0,
        "node_timings": {},
    }

    try:
        # Node 1
        yield sse({"type": "status", "node": "load_company_context",
                   "message": f"Loading {company.name} knowledge base..."})
        state.update(await load_company_context_node(state))

        # Node 2
        yield sse({"type": "status", "node": "retrieve_cases",
                   "message": "Searching resolved cases..."})
        state.update(await retrieve_cases_node(state))

        # Node 3
        if company.collection_id:
            yield sse({"type": "status", "node": "retrieve_rag",
                       "message": "Searching uploaded documents..."})
        state.update(await retrieve_rag_node(state))

        # Emit sources found
        yield sse({"type": "sources", "sources_found": state.get("sources_used", [])})

        # Node 4
        yield sse({"type": "status", "node": "build_context",
                   "message": "Assembling context..."})
        state.update(await build_context_node(state))

        # Node 5
        yield sse({"type": "status", "node": "generate_response",
                   "message": "Generating answer..."})
        state.update(await generate_response_node(state))

        # Node 6
        state.update(await check_escalation_node(state))

        # Conditional escalate
        if state.get("should_escalate"):
            state.update(await escalate_node(state))

        # Stream answer token by token
        answer = state.get("answer", "")
        words = answer.split(" ")
        for i, word in enumerate(words):
            yield sse({"type": "token", "content": word + (" " if i < len(words) - 1 else "")})

        # Persist
        try:
            db.add(CSMessage(session_id=session_id, role="user", content=request.message))
            db.add(CSMessage(
                session_id=session_id, role="assistant", content=answer,
                confidence=state.get("confidence", 0.0),
                sources_used=state.get("sources_used", []),
                should_escalate=state.get("should_escalate", False),
            ))
            if session:
                session.message_count = (session.message_count or 0) + 2
                if state.get("should_escalate"):
                    session.escalated = True
                    session.status = "escalated"
                    session.escalation_reason = state.get("escalation_reason")
            await db.commit()
        except Exception as e:
            log.warning("message persist failed (non-fatal)", error=str(e))

        yield sse({
            "type": "done",
            "session_id": str(session_id),
            "should_escalate": state.get("should_escalate", False),
            "escalation_reason": state.get("escalation_reason"),
            "confidence": state.get("confidence", 0.0),
            "sources_used": state.get("sources_used", []),
            "node_timings": state.get("node_timings", {}),
        })

    except Exception as e:
        log.exception("CS stream failed", error=str(e))
        yield sse({"type": "error", "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# CS AGENT — SESSIONS & ESCALATIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/cs/sessions", summary="List sessions for a company")
async def cs_list_sessions(
    company_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
):
    from app.models.cs import CSSession, CompanyRegistration

    # Verify ownership
    comp = await db.execute(
        select(CompanyRegistration).where(
            CompanyRegistration.id == company_id,
            CompanyRegistration.owner_id == current_user.id,
        )
    )
    if not comp.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Company not found")

    q = select(CSSession).where(CSSession.company_id == company_id)
    if status_filter:
        q = q.where(CSSession.status == status_filter)
    q = q.order_by(CSSession.last_message_at.desc()).limit(limit)

    res = await db.execute(q)
    sessions = res.scalars().all()

    return {
        "total": len(sessions),
        "sessions": [SessionSummary.model_validate(s) for s in sessions],
    }


@router.get("/cs/sessions/{session_id}", summary="Get session with full message history")
async def cs_get_session(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.models.cs import CSSession, CSMessage, CompanyRegistration

    sess_res = await db.execute(select(CSSession).where(CSSession.id == session_id))
    session = sess_res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify company ownership
    comp_res = await db.execute(
        select(CompanyRegistration).where(
            CompanyRegistration.id == session.company_id,
            CompanyRegistration.owner_id == current_user.id,
        )
    )
    if not comp_res.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    msg_res = await db.execute(
        select(CSMessage).where(CSMessage.session_id == session_id)
        .order_by(CSMessage.created_at.asc())
    )
    messages = msg_res.scalars().all()

    return SessionDetailResponse(
        session=SessionSummary.model_validate(session),
        messages=[MessageRecord.model_validate(m) for m in messages],
    )


@router.get("/cs/escalations", summary="List escalations for a company")
async def cs_escalations(
    company_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.models.cs import CSSession, CompanyRegistration

    comp_res = await db.execute(
        select(CompanyRegistration).where(
            CompanyRegistration.id == company_id,
            CompanyRegistration.owner_id == current_user.id,
        )
    )
    if not comp_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Company not found")

    q = select(CSSession).where(
        CSSession.company_id == company_id,
        CSSession.escalated == True,
    ).order_by(CSSession.last_message_at.desc())

    res = await db.execute(q)
    escalated = res.scalars().all()

    return EscalationSummary(
        total=len(escalated),
        unresolved=sum(1 for s in escalated if s.status == "escalated"),
        escalations=[
            {
                "session_id": str(s.id),
                "end_user_id": s.end_user_id,
                "reason": s.escalation_reason,
                "status": s.status,
                "message_count": s.message_count,
                "started_at": s.started_at,
            }
            for s in escalated
        ],
    )


@router.post("/cs/cases", summary="Save a resolved customer case")
async def cs_save_case(
    request: SaveResolvedCaseRequest,
    current_user: Annotated[User, Depends(get_current_user)],
):
    from app.services.case_history import save_resolved_case
    case_id = await save_resolved_case(
        company_id=str(request.company_id),
        problem=request.problem,
        resolution=request.resolution,
        category=request.category,
    )
    return {"case_id": case_id, "message": "Case saved. It will improve future CS agent responses."}


# ══════════════════════════════════════════════════════════════════════════════
# SOCIAL MEDIA AGENT (secondary — kept intact)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/social/compose", response_model=SocialComposeResponse,
             summary="[Social] Generate post — full pipeline")
async def social_compose(
    request: SocialComposeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> SocialComposeResponse:
    from app.agents.social_media import social_agent, SocialAgentState

    initial: SocialAgentState = {
        "user_id": str(current_user.id),
        "social_links": request.social_links,
        "niche": request.niche,
        "post_platform": request.platform,
        "post_tone": request.tone,
        "custom_instructions": request.custom_instructions,
        "raw_profiles": [], "user_profile": {},
        "detected_niche": request.niche or "",
        "raw_trends": [], "scored_trends": [], "selected_trend": {},
        "draft_post": "", "refined_post": "", "hashtags": [],
        "image_prompt": "", "post_variants": [],
        "quality_score": 0.0, "quality_feedback": "", "messages": [],
    }

    try:
        final = await social_agent.ainvoke(initial)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    post_text = final.get("refined_post") or final.get("draft_post", "")
    raw = final.get("user_profile", {})
    profile = UserProfile(
        niche=raw.get("niche", ""), style=raw.get("style", request.tone),
        audience=raw.get("audience", ""), topics=raw.get("topics", []),
        voice_samples=raw.get("voice_samples", []),
        content_gaps=raw.get("content_gaps", []),
        confidence=raw.get("confidence", "medium"),
    ) if raw else None

    top_trends = [TrendItem(**{k: t.get(k, "") for k in TrendItem.model_fields})
                  for t in final.get("scored_trends", [])[:5]]

    return SocialComposeResponse(
        post=post_text, platform=request.platform,
        hashtags=final.get("hashtags", []), image_prompt=final.get("image_prompt", ""),
        quality_score=final.get("quality_score", 0.0),
        quality_feedback=final.get("quality_feedback", ""),
        variants=final.get("post_variants", []),
        selected_trend=final.get("selected_trend", {}), top_trends=top_trends,
        detected_niche=final.get("detected_niche", ""), user_profile=profile,
    )


@router.post("/social/compose/stream", summary="[Social] Generate post — SSE stream")
async def social_compose_stream(
    request: SocialComposeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.agents.social_media import (
        SocialAgentState, analyze_user_node, enrich_profile_node,
        fetch_trends_node, score_trends_node, compose_post_node, refine_post_node,
    )

    async def _generate():
        def sse(d): return f"data: {json.dumps(d)}\n\n"
        state: SocialAgentState = {
            "user_id": str(current_user.id), "social_links": request.social_links,
            "niche": request.niche, "post_platform": request.platform,
            "post_tone": request.tone, "custom_instructions": request.custom_instructions,
            "raw_profiles": [], "user_profile": {}, "detected_niche": request.niche or "",
            "raw_trends": [], "scored_trends": [], "selected_trend": {},
            "draft_post": "", "refined_post": "", "hashtags": [], "image_prompt": "",
            "post_variants": [], "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }
        try:
            yield sse({"type": "status", "node": "analyze_user", "message": "Scraping profiles..."})
            state.update(await analyze_user_node(state))
            yield sse({"type": "status", "node": "enrich_profile", "message": "Analysing niche and style..."})
            state.update(await enrich_profile_node(state))
            yield sse({"type": "profile", "detected_niche": state.get("detected_niche", ""), "profile": state.get("user_profile", {})})
            yield sse({"type": "status", "node": "fetch_trends", "message": f"Finding trends in '{state.get('detected_niche', 'your niche')}'..."})
            state.update(await fetch_trends_node(state))
            state.update(await score_trends_node(state))
            yield sse({"type": "trends", "trends": state.get("scored_trends", [])[:5], "selected": state.get("selected_trend", {})})
            yield sse({"type": "status", "node": "compose_post", "message": f"Writing {request.platform} post..."})
            state.update(await compose_post_node(state))
            yield sse({"type": "status", "node": "refine_post", "message": "Running quality check..."})
            state.update(await refine_post_node(state))
            final_post = state.get("refined_post") or state.get("draft_post", "")
            for i, w in enumerate(final_post.split(" ")):
                yield sse({"type": "token", "content": w + (" " if i < len(final_post.split(" ")) - 1 else "")})
            yield sse({"type": "done", "hashtags": state.get("hashtags", []),
                       "image_prompt": state.get("image_prompt", ""),
                       "quality_score": state.get("quality_score", 0.0)})
        except Exception as e:
            yield sse({"type": "error", "error": str(e)})

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/social/trends", response_model=TrendsResponse, summary="[Social] Fetch trending topics")
async def social_trends(request: TrendsRequest, current_user: Annotated[User, Depends(get_current_user)]):
    from app.services.web_scraper import fetch_trending_news
    raw = await fetch_trending_news(niche=request.niche, limit=request.limit)
    trends = [TrendItem(**{k: t.get(k, "") for k in TrendItem.model_fields}) for t in raw]
    return TrendsResponse(niche=request.niche, count=len(trends), trends=trends)


@router.post("/social/refine", response_model=RefinePostResponse, summary="[Social] Refine a draft post")
async def social_refine(request: RefinePostRequest, current_user: Annotated[User, Depends(get_current_user)]):
    from app.agents.social_media import refine_post_node, SocialAgentState
    state: SocialAgentState = {
        "user_id": str(current_user.id), "social_links": [], "niche": None,
        "post_platform": request.platform, "post_tone": "professional",
        "custom_instructions": request.feedback, "raw_profiles": [], "user_profile": {},
        "detected_niche": "", "raw_trends": [], "scored_trends": [], "selected_trend": {},
        "draft_post": request.post, "refined_post": "", "hashtags": [], "image_prompt": "",
        "post_variants": [], "quality_score": 0.0, "quality_feedback": "", "messages": [],
    }
    result = await refine_post_node(state)
    return RefinePostResponse(original=request.post, refined=result.get("refined_post", request.post),
                              quality_score=result.get("quality_score", 0.0),
                              feedback=result.get("quality_feedback", ""), platform=request.platform)


@router.get("/social/platforms", response_model=PlatformsResponse, summary="[Social] Platform rules")
async def social_platforms():
    from app.agents.social_media import _PLATFORM_RULES
    return PlatformsResponse(platforms={
        n: PlatformInfo(char_limit=r["char_limit"], style=r["style"], hook_style=r["hook_style"])
        for n, r in _PLATFORM_RULES.items()
    })


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/health", summary="Agent subsystem health check")
async def agents_health():
    checks = {}
    for name, mod, attr in [
        ("cs_agent", "app.agents.customer_service", "cs_agent"),
        ("social_agent", "app.agents.social_media", "social_agent"),
        ("web_scraper", "app.services.web_scraper", "scrape_company_context"),
        ("case_history", "app.services.case_history", "find_similar_cases"),
    ]:
        try:
            import importlib
            getattr(importlib.import_module(mod), attr)
            checks[name] = "ok"
        except Exception as e:
            checks[name] = f"error: {e}"
    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}
