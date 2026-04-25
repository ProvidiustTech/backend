"""
evals/test_cs_agent.py
=======================
Tests for the Customer Service Agent (primary agent).
All external calls are mocked — no DB, LLM, or HTTP needed.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.customer_service import CSAgentState


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_SCRAPED_CONTEXT = """[MAIN — https://acme.com]
Acme Corp provides cloud-based inventory management for retail businesses.
Founded in 2018, we serve over 500 SMEs across Africa.

---

[ABOUT — https://acme.com/about]
We are a team of 40 engineers. Our mission is to make inventory management
simple and affordable for every retailer.

---

[FAQ — https://acme.com/faq]
Q: How much does Acme cost?
A: Plans start at $29/month for up to 5 users.

Q: Do you offer a free trial?
A: Yes! 14-day free trial, no credit card required.

Q: How do I cancel?
A: From Settings > Billing > Cancel Plan.
"""

SAMPLE_COMPANY_HTML = """
<html>
<head><title>Acme Corp - Cloud Inventory</title></head>
<body>
<nav><a href="/about">About</a> <a href="/faq">FAQ</a> <a href="/pricing">Pricing</a></nav>
<main>
<h1>Smart Inventory Management</h1>
<p>Acme helps 500+ retailers manage stock in real time.</p>
<p>Plans start at $29/month. 14-day free trial available.</p>
</main>
<footer>Contact: support@acme.com | Lagos, Nigeria</footer>
</body>
</html>
"""


def _make_state(**overrides) -> CSAgentState:
    base: CSAgentState = {
        "message": "How much does it cost?",
        "company_id": "company-123",
        "company_name": "Acme Corp",
        "company_url": "https://acme.com",
        "company_collection_id": None,
        "agent_name": "Aria",
        "system_prompt_override": "",
        "industry": "retail",
        "session_id": "session-abc",
        "end_user_id": "user-1",
        "chat_history": [],
        "messages": [],
        "scraped_context": SAMPLE_SCRAPED_CONTEXT,
        "scraped_pages": [],
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
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# WEB SCRAPER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestWebScraper:

    def test_text_extraction_removes_nav_and_scripts(self):
        from app.services.web_scraper import _extract_text
        html = """<html><body>
            <nav>Menu items nav</nav>
            <script>alert('skip me')</script>
            <style>.hide { display: none; }</style>
            <main><p>Real content here.</p><h2>Product Features</h2></main>
            <footer>Footer skip</footer>
        </body></html>"""
        text = _extract_text(html)
        assert "Real content here" in text
        assert "Product Features" in text
        assert "alert" not in text
        assert ".hide" not in text

    def test_base_url_extraction(self):
        from app.services.web_scraper import _base
        assert _base("https://acme.com/about") == "https://acme.com"
        assert _base("https://acme.com/faq?q=1") == "https://acme.com"
        assert _base("http://shop.acme.com/products") == "http://shop.acme.com"

    def test_link_discovery_finds_known_pages(self):
        from app.services.web_scraper import _discover_links
        html = """<html><body>
            <a href="/about-us">About Us</a>
            <a href="/faq">FAQ</a>
            <a href="/pricing">Pricing</a>
            <a href="/contact">Contact</a>
            <a href="https://other.com/page">External</a>
        </body></html>"""
        found = _discover_links(html, "https://acme.com")
        assert "about" in found
        assert "faq" in found
        assert "pricing" in found
        assert "contact" in found
        assert all("other.com" not in v for v in found.values())

    def test_link_discovery_handles_relative_paths(self):
        from app.services.web_scraper import _discover_links
        html = '<a href="/help/faq">Help</a>'
        found = _discover_links(html, "https://acme.com")
        if "faq" in found:
            assert found["faq"].startswith("https://acme.com")

    def test_resolve_url_same_domain(self):
        from app.services.web_scraper import _resolve
        assert _resolve("/about", "https://acme.com") == "https://acme.com/about"
        assert _resolve("https://acme.com/faq", "https://acme.com") == "https://acme.com/faq"

    def test_resolve_url_cross_domain_blocked(self):
        from app.services.web_scraper import _resolve
        assert _resolve("https://other.com/page", "https://acme.com") is None

    def test_resolve_url_mailto_blocked(self):
        from app.services.web_scraper import _resolve
        assert _resolve("mailto:hi@acme.com", "https://acme.com") is None

    def test_rss_parsing(self):
        from app.services.web_scraper import _parse_rss
        # Both titles are >10 chars so both should be parsed
        xml = """<?xml version="1.0"?>
        <rss><channel>
            <item>
                <title>Big Fintech News Today</title>
                <description>Something important happened in payments</description>
                <link>https://news.com/article1</link>
            </item>
            <item>
                <title>Another Important Story Here</title>
                <description>Details here</description>
                <link>https://news.com/article2</link>
            </item>
        </channel></rss>"""
        articles = _parse_rss(xml, limit=5)
        assert len(articles) == 2
        assert articles[0]["title"] == "Big Fintech News Today"
        assert articles[0]["url"] == "https://news.com/article1"

    def test_rss_skips_short_titles(self):
        from app.services.web_scraper import _parse_rss
        xml = """<rss><channel>
            <item><title>Hi</title><description>short</description></item>
            <item><title>This is a proper article title</title><description>content</description></item>
        </channel></rss>"""
        articles = _parse_rss(xml, limit=5)
        assert len(articles) == 1
        assert articles[0]["title"] == "This is a proper article title"

    @pytest.mark.asyncio
    async def test_scrape_company_context_uses_cache(self):
        from app.services.web_scraper import _cache_set, _ck, scrape_company_context
        ck = _ck("company_context", "https://cached.com")
        _cache_set(ck, ("Cached context text", []), ttl=3600)
        with patch("app.services.web_scraper._fetch") as mock_fetch:
            result_text, results = await scrape_company_context(
                url="https://cached.com", company_id="c1"
            )
            mock_fetch.assert_not_called()
        assert result_text == "Cached context text"

    @pytest.mark.asyncio
    async def test_scrape_company_context_live(self):
        from app.services.web_scraper import scrape_company_context, _cache
        _cache.clear()
        with patch("app.services.web_scraper._fetch") as mock_fetch:
            mock_fetch.side_effect = [
                SAMPLE_COMPANY_HTML,
                "<html><body><p>About Acme Corp, founded 2018.</p></body></html>",
                "<html><body><p>FAQ: Plans start at $29/month.</p></body></html>",
            ]
            combined, results = await scrape_company_context(
                url="https://acme.com", company_id="company-123", force_refresh=True
            )
        assert "Acme" in combined or "Smart Inventory" in combined
        assert len(results) > 0
        assert results[0].page_type == "main"


# ══════════════════════════════════════════════════════════════════════════════
# CS AGENT NODE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadCompanyContextNode:

    @pytest.mark.asyncio
    async def test_uses_existing_context(self):
        from app.agents.customer_service import load_company_context_node
        state = _make_state(scraped_context=SAMPLE_SCRAPED_CONTEXT)
        # Patch at the source module, not at the import site
        with patch("app.services.web_scraper.scrape_company_context") as mock_scrape:
            result = await load_company_context_node(state)
            mock_scrape.assert_not_called()
        assert result["scraped_context"] == SAMPLE_SCRAPED_CONTEXT
        assert "web_scrape" in result["sources_used"]

    @pytest.mark.asyncio
    async def test_triggers_live_scrape_when_empty(self):
        from app.agents.customer_service import load_company_context_node
        from app.services.web_scraper import ScrapeResult

        state = _make_state(scraped_context="", company_url="https://acme.com")
        mock_result = ScrapeResult(url="https://acme.com", page_type="main", text="Live content")

        # Patch at the source module where the function is defined
        with patch(
            "app.services.web_scraper.scrape_company_context",
            new=AsyncMock(return_value=("Live content from scrape", [mock_result])),
        ):
            result = await load_company_context_node(state)

        assert "Live content" in result["scraped_context"]
        assert "web_scrape" in result["sources_used"]

    @pytest.mark.asyncio
    async def test_no_url_no_crash(self):
        from app.agents.customer_service import load_company_context_node
        state = _make_state(scraped_context="", company_url=None)
        result = await load_company_context_node(state)
        assert result["scraped_context"] == ""


class TestBuildContextNode:

    @pytest.mark.asyncio
    async def test_confidence_with_all_sources(self):
        from app.agents.customer_service import build_context_node
        state = _make_state(
            scraped_context=SAMPLE_SCRAPED_CONTEXT,
            resolved_cases=[{"problem": "billing", "resolution": "refunded", "score": 0.9}],
            rag_chunks=[{"text": "Extra doc content", "score": 0.8, "metadata": {}}],
        )
        result = await build_context_node(state)
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_confidence_scrape_only(self):
        from app.agents.customer_service import build_context_node
        state = _make_state(scraped_context=SAMPLE_SCRAPED_CONTEXT, resolved_cases=[], rag_chunks=[])
        result = await build_context_node(state)
        assert result["confidence"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_confidence_no_sources(self):
        from app.agents.customer_service import build_context_node
        state = _make_state(scraped_context="", resolved_cases=[], rag_chunks=[])
        result = await build_context_node(state)
        assert result["confidence"] == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_assembled_context_has_sections(self):
        from app.agents.customer_service import build_context_node
        state = _make_state(
            scraped_context=SAMPLE_SCRAPED_CONTEXT,
            resolved_cases=[{"problem": "login issue", "resolution": "reset password", "score": 0.8}],
            rag_chunks=[],
        )
        result = await build_context_node(state)
        ctx = result["assembled_context"]
        assert "Company Knowledge" in ctx


class TestCheckEscalationNode:

    @pytest.mark.asyncio
    async def test_explicit_human_request(self):
        from app.agents.customer_service import check_escalation_node
        for phrase in ["speak to a human", "talk to a person", "manager", "supervisor"]:
            state = _make_state(message=f"I want to {phrase} please", answer="I'll help!", confidence=0.8)
            result = await check_escalation_node(state)
            assert result["should_escalate"] is True, f"Failed for: '{phrase}'"

    @pytest.mark.asyncio
    async def test_legal_keywords_escalate(self):
        from app.agents.customer_service import check_escalation_node
        for phrase in ["legal action", "lawsuit", "fraud", "chargeback"]:
            state = _make_state(message=f"I'm going to take {phrase}", answer="I'll help!", confidence=0.8)
            result = await check_escalation_node(state)
            assert result["should_escalate"] is True, f"Failed for: '{phrase}'"

    @pytest.mark.asyncio
    async def test_low_confidence_escalates(self):
        from app.agents.customer_service import check_escalation_node
        state = _make_state(message="Normal question", answer="I think so", confidence=0.3)
        result = await check_escalation_node(state)
        assert result["should_escalate"] is True
        assert "confidence" in result["escalation_reason"].lower()

    @pytest.mark.asyncio
    async def test_normal_conversation_no_escalation(self):
        from app.agents.customer_service import check_escalation_node
        state = _make_state(
            message="How do I reset my password?",
            answer="Click Forgot Password on the login page and follow the steps. Is there anything else I can help you with?",
            confidence=0.85,
        )
        result = await check_escalation_node(state)
        assert result["should_escalate"] is False
        assert result["escalation_reason"] is None

    @pytest.mark.asyncio
    async def test_frustration_escalates(self):
        from app.agents.customer_service import check_escalation_node
        state = _make_state(
            message="This is unacceptable! Your service is terrible!",
            answer="I apologise...", confidence=0.8,
        )
        result = await check_escalation_node(state)
        assert result["should_escalate"] is True

    @pytest.mark.asyncio
    async def test_escalate_node_appends_reference(self):
        from app.agents.customer_service import escalate_node
        state = _make_state(answer="Here is what I found.", session_id="abc123def456")
        result = await escalate_node(state)
        assert "ABC123DE" in result["answer"]
        assert "team" in result["answer"].lower() or "agent" in result["answer"].lower()


# ══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestFullCSPipeline:

    @pytest.mark.asyncio
    async def test_full_pipeline_no_escalation(self):
        from app.agents.customer_service import (
            load_company_context_node, retrieve_cases_node, retrieve_rag_node,
            build_context_node, generate_response_node, check_escalation_node,
        )
        state = _make_state(message="What does Acme cost?", scraped_context=SAMPLE_SCRAPED_CONTEXT)
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="Acme plans start at $29/month. 14-day free trial included. "
                    "Is there anything else I can help you with?"
        )
        with patch("app.agents.customer_service.get_llm", return_value=mock_llm), \
             patch("app.services.case_history.find_similar_cases", new=AsyncMock(return_value=[])):
            state.update(await load_company_context_node(state))
            state.update(await retrieve_cases_node(state))
            state.update(await retrieve_rag_node(state))
            state.update(await build_context_node(state))
            state.update(await generate_response_node(state))
            state.update(await check_escalation_node(state))

        assert "$29" in state["answer"]
        assert state["should_escalate"] is False
        assert state["confidence"] >= 0.7
        assert "web_scrape" in state["sources_used"]

    @pytest.mark.asyncio
    async def test_full_pipeline_with_escalation(self):
        from app.agents.customer_service import (
            load_company_context_node, retrieve_cases_node, retrieve_rag_node,
            build_context_node, generate_response_node, check_escalation_node, escalate_node,
        )
        state = _make_state(
            message="I want to speak to a manager right now!",
            scraped_context=SAMPLE_SCRAPED_CONTEXT,
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="Let me connect you with our team.")
        with patch("app.agents.customer_service.get_llm", return_value=mock_llm), \
             patch("app.services.case_history.find_similar_cases", new=AsyncMock(return_value=[])):
            state.update(await load_company_context_node(state))
            state.update(await retrieve_cases_node(state))
            state.update(await retrieve_rag_node(state))
            state.update(await build_context_node(state))
            state.update(await generate_response_node(state))
            state.update(await check_escalation_node(state))
            assert state["should_escalate"] is True
            state.update(await escalate_node(state))

        assert state["should_escalate"] is True
        assert "SESSION-ABC"[:3] in state["answer"].upper() or "team" in state["answer"].lower()


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestCSSchemas:

    def test_company_register_request(self):
        from app.schemas.cs import CompanyRegisterRequest
        req = CompanyRegisterRequest(
            company_name="Acme Corp",
            company_url="https://acme.com",
            industry="retail",
            agent_name="Aria",
        )
        assert req.company_name == "Acme Corp"
        assert req.agent_name == "Aria"

    def test_cs_chat_response(self):
        from app.schemas.cs import CSChatResponse, SourceUsed
        import uuid
        resp = CSChatResponse(
            answer="Plans start at $29/month.",
            session_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            should_escalate=False,
            escalation_reason=None,
            confidence=0.85,
            sources_used=[SourceUsed(type="web_scrape", label="Company website", confidence=0.9)],
        )
        assert resp.confidence == 0.85
        assert len(resp.sources_used) == 1

    def test_session_summary_from_orm(self):
        from app.schemas.cs import SessionSummary
        from datetime import datetime, timezone
        import uuid

        # The ORM model uses `id` — SessionSummary maps it via alias
        class FakeSession:
            id = uuid.uuid4()           # ORM column is `id`
            status = "active"
            message_count = 4
            escalated = False
            started_at = datetime.now(timezone.utc)
            last_message_at = datetime.now(timezone.utc)

        # Use model_validate with from_attributes=True
        summary = SessionSummary.model_validate(FakeSession(), from_attributes=True)
        assert summary.session_id == FakeSession.id
        assert summary.status == "active"
        assert summary.message_count == 4