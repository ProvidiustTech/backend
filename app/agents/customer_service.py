"""
app/agents/customer_service.py
================================
Customer Service AI Agent — PRIMARY agent, LangGraph-powered.

Data sources (priority order):
  1. Web scrape of company URL         ← PRIMARY (About, FAQ, Pricing, Contact)
  2. Previously resolved cases         ← SECONDARY (similar past problems)
  3. Uploaded company documents (RAG)  ← TERTIARY (optional enrichment)
  4. LLM general knowledge             ← FALLBACK only (low confidence flag)

LangGraph pipeline:
  START
    → load_company_context    (get scraped content from DB cache or live scrape)
    → retrieve_cases          (find similar resolved cases)
    → retrieve_rag            (search pgvector docs if collection exists)
    → build_context           (assemble + rank all sources)
    → generate_response       (LLM grounded answer with multi-turn memory)
    → check_escalation        (keyword + confidence-based escalation detection)
    → [escalate]              (conditional — appends escalation message)
    → END

Extending:
  To add Tool Calling (e.g. live order lookup):
    graph.add_node("tool_call", tool_call_node)
    graph.add_edge("build_context", "tool_call")
    graph.add_edge("tool_call", "generate_response")
"""

import json
import re
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.core.config import settings
from app.core.logging import get_logger
from app.services.llm import get_llm

log = get_logger(__name__)


# ── State ──────────────────────────────────────────────────────────────────────

class CSAgentState(TypedDict):
    # ── Input ──────────────────────────────────────────────────────────────────
    message: str                     # current customer message
    company_id: str
    company_name: str
    company_url: str | None
    company_collection_id: str | None   # pgvector collection for uploaded docs
    agent_name: str                  # e.g. "Aria" or "Support Agent"
    system_prompt_override: str      # blank = use default
    industry: str | None             # finance | healthcare | retail | ...
    session_id: str
    end_user_id: str

    # ── Conversation memory ────────────────────────────────────────────────────
    chat_history: list[dict]         # [{"role": "user"|"assistant", "content": "..."}]
    messages: Annotated[list[BaseMessage], add_messages]

    # ── Context assembly ───────────────────────────────────────────────────────
    training_data: str               # custom text from onboarding
    scraped_context: str             # from web scrape (primary source)
    scraped_pages: list[dict]        # metadata about which pages were scraped
    resolved_cases: list[dict]       # similar past cases
    rag_chunks: list[dict]           # from pgvector (tertiary)
    assembled_context: str           # combined, ranked, ready for prompt
    sources_used: list[str]          # ["web_scrape", "resolved_cases", "rag"]

    # ── Output ────────────────────────────────────────────────────────────────
    answer: str
    confidence: float                # 0.0–1.0
    should_escalate: bool
    escalation_reason: str | None
    hallucination_score: float

    # ── Timing ────────────────────────────────────────────────────────────────
    node_timings: dict[str, float]   # ms per node


# ── Node 1: load_company_context ───────────────────────────────────────────────

async def load_company_context_node(state: CSAgentState) -> dict:
    """
    PRIMARY DATA SOURCE — Web scrape of the company URL.

    Flow:
      1. Check if scraped_context already in state (populated from DB cache by API layer)
      2. If empty, trigger a live scrape via scrape_company_context()
      3. Scraped content covers: main page, About, FAQ, Pricing, Contact

    The API layer pre-populates scraped_context from DB if it's fresh (<24h old),
    so this node usually just validates and passes it through. It triggers a live
    scrape only if the cache is cold or force_refresh was requested.
    """
    t0 = time.perf_counter()

    scraped = state.get("scraped_context", "")
    pages = state.get("scraped_pages", [])

    if not scraped and state.get("company_url"):
        log.info("cold scrape triggered", company_id=state["company_id"])
        from app.services.web_scraper import scrape_company_context

        combined, results = await scrape_company_context(
            url=state["company_url"],
            company_id=state["company_id"],
        )
        scraped = combined
        pages = [{"url": r.url, "page_type": r.page_type, "chars": r.char_count} for r in results]

    elapsed = (time.perf_counter() - t0) * 1000
    sources = state.get("sources_used", [])
    if scraped:
        sources = list(set(sources + ["web_scrape"]))

    log.info(
        "company context loaded",
        chars=len(scraped),
        pages=len(pages),
        company_id=state["company_id"],
    )

    return {
        "scraped_context": scraped,
        "scraped_pages": pages,
        "sources_used": sources,
        "node_timings": {**state.get("node_timings", {}), "load_context_ms": elapsed},
    }


# ── Node 2: retrieve_cases ─────────────────────────────────────────────────────

async def retrieve_cases_node(state: CSAgentState) -> dict:
    """
    SECONDARY SOURCE — Similar resolved cases from the database.
    Finds past Q&A pairs that match the current customer query.
    Low overhead — keyword overlap scoring, no vector search needed here.
    """
    t0 = time.perf_counter()

    try:
        from app.services.case_history import find_similar_cases
        cases = await find_similar_cases(
            query=state["message"],
            company_id=state["company_id"],
            limit=3,
        )
    except Exception as e:
        log.warning("case retrieval failed", error=str(e))
        cases = []

    elapsed = (time.perf_counter() - t0) * 1000
    sources = state.get("sources_used", [])
    if cases:
        sources = list(set(sources + ["resolved_cases"]))

    return {
        "resolved_cases": cases,
        "sources_used": sources,
        "node_timings": {**state.get("node_timings", {}), "retrieve_cases_ms": elapsed},
    }


# ── Node 3: retrieve_rag ───────────────────────────────────────────────────────

async def retrieve_rag_node(state: CSAgentState) -> dict:
    """
    TERTIARY SOURCE — pgvector search over uploaded company documents.
    Only runs if a collection_id was configured for this company.
    Skips gracefully if no collection exists.
    """
    t0 = time.perf_counter()
    collection_id = state.get("company_collection_id")

    if not collection_id:
        return {
            "rag_chunks": [],
            "node_timings": {**state.get("node_timings", {}), "retrieve_rag_ms": 0},
        }

    try:
        from app.services.vector_store import get_vector_store
        store = await get_vector_store(collection_id)
        results = await store.asimilarity_search_with_relevance_scores(
            state["message"], k=5
        )
        chunks = [
            {"text": doc.page_content, "score": float(score), "metadata": doc.metadata}
            for doc, score in results
            if float(score) >= settings.SIMILARITY_THRESHOLD
        ]
    except Exception as e:
        log.warning("RAG retrieval failed", error=str(e))
        chunks = []

    elapsed = (time.perf_counter() - t0) * 1000
    sources = state.get("sources_used", [])
    if chunks:
        sources = list(set(sources + ["rag_documents"]))

    return {
        "rag_chunks": chunks,
        "sources_used": sources,
        "node_timings": {**state.get("node_timings", {}), "retrieve_rag_ms": elapsed},
    }


# ── Node 4: build_context ──────────────────────────────────────────────────────

async def build_context_node(state: CSAgentState) -> dict:
    """
    Assembles all sources into a single structured context string for the LLM.

    Priority / space allocation:
      - Web scrape:       up to 4000 chars  (primary — most reliable)
      - Resolved cases:   up to 1500 chars  (secondary — proven answers)
      - RAG documents:    up to 1500 chars  (tertiary — optional enrichment)

    Total context sent to LLM: ~7000 chars max.
    This keeps the prompt within safe token limits for all supported models.
    """
    t0 = time.perf_counter()
    parts: list[str] = []

    # Custom Training Data (highest priority for specific instructions/facts)
    training = state.get("training_data", "")
    if training:
        parts.append(f"## Custom Company Knowledge (Manual Upload)\n\n{training[:4000]}")

    # Primary: web scrape
    scraped = state.get("scraped_context", "")
    if scraped:
        parts.append(f"## Company Knowledge (from website)\n\n{scraped[:4000]}")

    # Secondary: resolved cases
    cases = state.get("resolved_cases", [])
    if cases:
        case_texts = []
        for c in cases[:3]:
            case_texts.append(
                f"Past case (score: {c.get('score', 0):.2f}):\n"
                f"Problem: {c.get('problem', '')}\n"
                f"Resolution: {c.get('resolution', '')}"
            )
        parts.append("## Similar Resolved Cases\n\n" + "\n\n".join(case_texts))

    # Tertiary: RAG chunks
    rag = state.get("rag_chunks", [])
    if rag:
        rag_texts = [f"[doc] {c['text'][:400]}" for c in rag[:3]]
        parts.append("## Additional Documents\n\n" + "\n\n".join(rag_texts))

    assembled = "\n\n---\n\n".join(parts) if parts else ""

    # Compute confidence based on source richness
    confidence = 0.3  # base: LLM knowledge only
    if scraped:
        confidence += 0.4   # +40% if we have actual company content
    if cases:
        confidence += 0.15  # +15% if we have proven past resolutions
    if rag:
        confidence += 0.15  # +15% if we have additional documents
    confidence = min(confidence, 0.95)

    elapsed = (time.perf_counter() - t0) * 1000

    return {
        "assembled_context": assembled,
        "confidence": confidence,
        "node_timings": {**state.get("node_timings", {}), "build_context_ms": elapsed},
    }


# ── Node 5: generate_response ──────────────────────────────────────────────────

async def generate_response_node(state: CSAgentState) -> dict:
    """
    Generate a grounded customer service response.

    Uses:
    - assembled_context (web scrape + cases + RAG)
    - chat_history (last 6 turns for multi-turn memory)
    - company identity (name, agent_name, industry)
    - system_prompt_override (if company has custom prompt)
    """
    t0 = time.perf_counter()
    llm = get_llm(temperature=0.15)  # low temp = consistent, factual answers

    context = state.get("assembled_context", "")
    company_name = state.get("company_name", "the company")
    agent_name = state.get("agent_name", "Support Agent")
    industry = state.get("industry", "")
    confidence = state.get("confidence", 0.3)

    # Build conversation history context
    history_text = ""
    history = state.get("chat_history", [])[-6:]  # last 3 turns (6 messages)
    if history:
        lines = []
        for turn in history:
            role = "Customer" if turn.get("role") == "user" else agent_name
            lines.append(f"{role}: {turn.get('content', '')}")
        history_text = "\n".join(lines)

    # Industry-specific guidance
    industry_notes = {
        "finance":       "Be precise. Never invent rates, limits, or regulatory details. Always caveat financial advice.",
        "healthcare":    "Be empathetic. Always recommend professional consultation for medical decisions.",
        "retail":        "Be friendly and solution-focused. Prioritise customer satisfaction.",
        "manufacturing": "Be technical and precise. Reference safety guidelines when relevant.",
        "logistics":     "Be operational and efficient. Provide tracking details and timelines where available.",
    }
    industry_guidance = industry_notes.get(industry or "", "Be helpful, clear, and professional.")

    # Use override prompt or default
    if state.get("system_prompt_override"):
        system_content = state["system_prompt_override"]
    else:
        system_content = f"""You are {agent_name}, the AI customer service assistant for {company_name}.

KNOWLEDGE BASE (use ONLY this to answer — do not invent facts):
{context if context else "No company knowledge loaded yet."}

{"PREVIOUS CONVERSATION:" + chr(10) + history_text if history_text else ""}

GUIDELINES:
- Answer based ONLY on the knowledge base above
- If the knowledge base doesn't cover the question, say so honestly
- Be {industry_guidance.lower()}
- {industry_guidance}
- Never invent policies, prices, features, or contact details
- Cite which section the information came from when helpful
- End responses by asking if there's anything else you can help with
- Keep answers concise — under 200 words unless detail is needed
- If confidence is low ({confidence:.0%}), acknowledge that you may need to verify with the team"""

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=state["message"]),
    ]

    try:
        response = await llm.ainvoke(messages)
        answer = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        log.error("LLM generation failed", error=str(e))
        answer = (
            f"I'm experiencing a technical issue right now. "
            f"Please contact {company_name} directly or try again in a moment."
        )

    elapsed = (time.perf_counter() - t0) * 1000
    log.info("response generated", chars=len(answer), confidence=confidence, elapsed_ms=round(elapsed))

    return {
        "answer": answer,
        "messages": [HumanMessage(content=state["message"]), AIMessage(content=answer)],
        "node_timings": {**state.get("node_timings", {}), "generate_ms": elapsed},
    }


# ── Node 6: check_escalation ───────────────────────────────────────────────────

async def check_escalation_node(state: CSAgentState) -> dict:
    """
    Decides if this conversation should be escalated to a human agent.

    Triggers (in order of priority):
      1. Customer explicitly requests human / manager / supervisor
      2. Legal / billing / refund keywords above a threshold
      3. Frustration signals (repeated words, anger phrases)
      4. AI confidence is too low to answer reliably (<40%)
      5. Answer contains "I don't know" / "I can't find" signals
    """
    message_lower = state["message"].lower()
    answer_lower = state.get("answer", "").lower()
    confidence = state.get("confidence", 1.0)

    # Priority 1: Explicit human request
    human_request_kws = [
        "speak to a human", "talk to a person", "real person", "human agent",
        "live agent", "speak to someone", "manager", "supervisor", "escalate",
        "i demand", "get me a human", "call me", "phone number",
    ]
    for kw in human_request_kws:
        if kw in message_lower:
            return {
                "should_escalate": True,
                "escalation_reason": f"Customer requested: '{kw}'",
                "node_timings": state.get("node_timings", {}),
            }

    # Priority 2: Legal / serious billing
    serious_kws = [
        "legal action", "lawsuit", "solicitor", "lawyer", "court",
        "fraud", "stolen", "unauthorized charge", "chargeback",
        "data breach", "my data", "gdpr", "i want a refund",
    ]
    for kw in serious_kws:
        if kw in message_lower:
            return {
                "should_escalate": True,
                "escalation_reason": f"Serious issue requiring human review: '{kw}'",
                "node_timings": state.get("node_timings", {}),
            }

    # Priority 3: Frustration signals
    frustration_kws = [
        "this is unacceptable", "terrible service", "worst company",
        "absolutely useless", "i am furious", "disgusting",
        "i am appalled", "deeply disappointed",
    ]
    for kw in frustration_kws:
        if kw in message_lower:
            return {
                "should_escalate": True,
                "escalation_reason": "Customer expressing strong frustration",
                "node_timings": state.get("node_timings", {}),
            }

    # Priority 4: Low confidence
    if confidence < 0.4:
        return {
            "should_escalate": True,
            "escalation_reason": f"Agent confidence too low ({confidence:.0%}) — no reliable knowledge base content",
            "node_timings": state.get("node_timings", {}),
        }

    # Priority 5: Answer signals it couldn't help
    cant_help = ["i don't have", "i cannot find", "i'm unable to", "not in my knowledge", "i'm not sure about"]
    if any(s in answer_lower for s in cant_help):
        # Only escalate if it's not a soft "let me know if..." type response
        if "can i help" not in answer_lower and "anything else" not in answer_lower:
            return {
                "should_escalate": True,
                "escalation_reason": "Knowledge base insufficient to answer this query",
                "node_timings": state.get("node_timings", {}),
            }

    return {
        "should_escalate": False,
        "escalation_reason": None,
        "node_timings": state.get("node_timings", {}),
    }


# ── Node 7: escalate ──────────────────────────────────────────────────────────

async def escalate_node(state: CSAgentState) -> dict:
    """
    Appends an escalation notice to the answer.
    In production, this node would also:
      - Create a ticket in the company's CRM
      - Send an email to escalation_email
      - Route the session to a live agent queue
    """
    ref = state["session_id"][:8].upper()
    agent_name = state.get("agent_name", "Support Agent")

    escalation_msg = (
        "\n\n---\n"
        f"I'm connecting you with a member of our team who can help further. "
        f"Your reference number is **{ref}** — please have this ready. "
        f"A human agent will be with you shortly."
    )

    log.info(
        "escalation triggered",
        company_id=state["company_id"],
        reason=state.get("escalation_reason"),
        session_id=state["session_id"],
    )

    return {"answer": state.get("answer", "") + escalation_msg}


# ── Routing ────────────────────────────────────────────────────────────────────

def _route_escalation(state: CSAgentState) -> str:
    return "escalate" if state.get("should_escalate") else END


# ── Graph Builder ──────────────────────────────────────────────────────────────

def build_cs_agent() -> StateGraph:
    """
    Compile the Customer Service Agent graph.

    Full flow:
      START
        → load_company_context    (web scrape — PRIMARY source)
        → retrieve_cases          (past resolutions — SECONDARY source)
        → retrieve_rag            (uploaded docs — TERTIARY source, optional)
        → build_context           (assemble + confidence score all sources)
        → generate_response       (grounded LLM answer with memory)
        → check_escalation        (keyword + confidence routing)
        → escalate (conditional)  (append escalation message + log)
        → END

    To extend with live data tools (order lookup, CRM query):
      graph.add_node("tool_call", your_tool_node)
      # Insert between build_context and generate_response:
      graph.add_edge("build_context", "tool_call")
      graph.add_edge("tool_call", "generate_response")
    """
    g = StateGraph(CSAgentState)

    g.add_node("load_company_context", load_company_context_node)
    g.add_node("retrieve_cases",       retrieve_cases_node)
    g.add_node("retrieve_rag",         retrieve_rag_node)
    g.add_node("build_context",        build_context_node)
    g.add_node("generate_response",    generate_response_node)
    g.add_node("check_escalation",     check_escalation_node)
    g.add_node("escalate",             escalate_node)

    g.add_edge(START,                    "load_company_context")
    g.add_edge("load_company_context",   "retrieve_cases")
    g.add_edge("retrieve_cases",         "retrieve_rag")
    g.add_edge("retrieve_rag",           "build_context")
    g.add_edge("build_context",          "generate_response")
    g.add_edge("generate_response",      "check_escalation")
    g.add_conditional_edges(
        "check_escalation",
        _route_escalation,
        {"escalate": "escalate", END: END},
    )
    g.add_edge("escalate", END)

    return g.compile()


cs_agent = build_cs_agent()
