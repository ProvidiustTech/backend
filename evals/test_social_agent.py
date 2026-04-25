"""
evals/test_social_agent.py
===========================
Tests for the Social Media Agent pipeline.

Covers:
  - Profile enrichment with and without scraped data
  - Trend scoring logic
  - Post composition for each platform
  - Refine node quality thresholds
  - JSON parse helper robustness
  - Full pipeline state flow (mocked scraper + LLM)
  - API response schema validation
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

MOCK_PROFILE = {
    "url": "https://linkedin.com/in/testuser",
    "platform": "linkedin",
    "username": "testuser",
    "bio": "Fintech founder. Building the future of payments in Africa.",
    "page_title": "Test User | LinkedIn",
    "topics_hint": ["fintech", "payments", "africa", "startup", "founder"],
    "og_data": {"description": "Fintech founder building payments for Africa"},
    "raw_snippet": "Fintech founder. 10 years in banking. Now building Payvest.",
}

MOCK_TRENDS = [
    {"title": "Open Banking APIs reshape African fintech landscape",
     "summary": "New CBN regulations open the door for API-first banking",
     "url": "https://example.com/1", "relevance_score": 0.9, "source": "rss"},
    {"title": "Stablecoin adoption surges across West Africa",
     "summary": "USDC volumes triple in Nigeria and Ghana",
     "url": "https://example.com/2", "relevance_score": 0.85, "source": "rss"},
    {"title": "Global tech stocks rally on AI earnings",
     "summary": "S&P tech sector up 3%",
     "url": "https://example.com/3", "relevance_score": 0.3, "source": "rss"},
]

SAMPLE_LINKEDIN_POST = """
The CBN's Open Banking framework just changed everything for African fintech.

3 years ago, I spent 6 months building bank integrations that broke every quarter.
Today, a developer can connect to any Nigerian bank in 3 days with a single API.

The boring infrastructure work is finally done.

Now the question isn't "can we build it?" — it's "what do we build on top of it?"

What's the one product you'd build if every bank in Africa had a public API?

#OpenBanking #Fintech #Africa #Payments #Startup
"""


# ── Unit: _safe_json ──────────────────────────────────────────────────────────

class TestSafeJson:

    def test_clean_json(self):
        from app.agents.social_media import _safe_json
        result = _safe_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_strips_markdown_fences(self):
        from app.agents.social_media import _safe_json
        result = _safe_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_strips_plain_fences(self):
        from app.agents.social_media import _safe_json
        result = _safe_json('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_finds_json_within_text(self):
        from app.agents.social_media import _safe_json
        result = _safe_json('Here is the result: {"niche": "fintech"} as requested')
        assert result == {"niche": "fintech"}

    def test_returns_fallback_on_invalid(self):
        from app.agents.social_media import _safe_json
        result = _safe_json("this is not json at all", fallback={"default": True})
        assert result == {"default": True}

    def test_parses_array(self):
        from app.agents.social_media import _safe_json
        result = _safe_json('[{"title": "test"}]')
        assert isinstance(result, list)
        assert result[0]["title"] == "test"


# ── Unit: Platform rules ──────────────────────────────────────────────────────

class TestPlatformRules:

    def test_all_platforms_present(self):
        from app.agents.social_media import _PLATFORM_RULES
        for platform in ["twitter", "linkedin", "instagram", "threads", "facebook"]:
            assert platform in _PLATFORM_RULES

    def test_twitter_char_limit(self):
        from app.agents.social_media import _PLATFORM_RULES
        assert _PLATFORM_RULES["twitter"]["char_limit"] == 280

    def test_linkedin_char_limit(self):
        from app.agents.social_media import _PLATFORM_RULES
        assert _PLATFORM_RULES["linkedin"]["char_limit"] == 3000

    def test_all_have_required_keys(self):
        from app.agents.social_media import _PLATFORM_RULES
        required = {"char_limit", "style", "format", "hook_style"}
        for name, rules in _PLATFORM_RULES.items():
            for key in required:
                assert key in rules, f"'{name}' missing key '{key}'"


# ── Unit: enrich_profile node (mocked LLM) ───────────────────────────────────

class TestEnrichProfileNode:

    @pytest.mark.asyncio
    async def test_with_profiles(self):
        from app.agents.social_media import enrich_profile_node, SocialAgentState

        state: SocialAgentState = {
            "user_id": "user-1",
            "social_links": ["https://linkedin.com/in/test"],
            "niche": None,
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "",
            "raw_profiles": [MOCK_PROFILE],
            "user_profile": {}, "detected_niche": "",
            "raw_trends": [], "scored_trends": [], "selected_trend": {},
            "draft_post": "", "refined_post": "", "hashtags": [],
            "image_prompt": "", "post_variants": [],
            "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"niche": "fintech startup founder", "style": "professional", '
                    '"audience": "African fintech professionals", "topics": ["payments", "fintech"], '
                    '"voice_samples": [], "content_gaps": [], "confidence": "high"}'
        )

        with patch("app.agents.social_media.get_llm", return_value=mock_llm):
            result = await enrich_profile_node(state)

        assert "user_profile" in result
        assert "detected_niche" in result
        assert result["detected_niche"] == "fintech startup founder"
        assert result["user_profile"]["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_no_profiles_fallback(self):
        from app.agents.social_media import enrich_profile_node, SocialAgentState

        state: SocialAgentState = {
            "user_id": "user-1",
            "social_links": [],
            "niche": "AI engineer",
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "",
            "raw_profiles": [],
            "user_profile": {}, "detected_niche": "",
            "raw_trends": [], "scored_trends": [], "selected_trend": {},
            "draft_post": "", "refined_post": "", "hashtags": [],
            "image_prompt": "", "post_variants": [],
            "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }

        result = await enrich_profile_node(state)
        assert result["detected_niche"] == "AI engineer"
        assert result["user_profile"]["confidence"] == "low"

    @pytest.mark.asyncio
    async def test_explicit_niche_always_wins(self):
        """Explicit niche override should trump LLM detection."""
        from app.agents.social_media import enrich_profile_node, SocialAgentState

        state: SocialAgentState = {
            "user_id": "user-1", "social_links": [],
            "niche": "logistics tech",
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "", "raw_profiles": [MOCK_PROFILE],
            "user_profile": {}, "detected_niche": "", "raw_trends": [],
            "scored_trends": [], "selected_trend": {}, "draft_post": "",
            "refined_post": "", "hashtags": [], "image_prompt": "",
            "post_variants": [], "quality_score": 0.0, "quality_feedback": "",
            "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"niche": "fintech", "style": "professional", "audience": "founders", '
                    '"topics": ["fintech"], "voice_samples": [], "content_gaps": [], "confidence": "medium"}'
        )

        with patch("app.agents.social_media.get_llm", return_value=mock_llm):
            result = await enrich_profile_node(state)

        # Explicit niche wins over LLM detected "fintech"
        assert result["detected_niche"] == "logistics tech"
        assert result["user_profile"]["niche"] == "logistics tech"


# ── Unit: score_trends node ───────────────────────────────────────────────────

class TestScoreTrendsNode:

    @pytest.mark.asyncio
    async def test_scores_and_selects_best(self):
        from app.agents.social_media import score_trends_node, SocialAgentState

        state: SocialAgentState = {
            "user_id": "u1", "social_links": [], "niche": "fintech",
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "",
            "raw_profiles": [], "user_profile": {"niche": "fintech", "audience": "founders",
                                                   "style": "professional", "topics": ["fintech"]},
            "detected_niche": "fintech",
            "raw_trends": MOCK_TRENDS,
            "scored_trends": [], "selected_trend": {}, "draft_post": "",
            "refined_post": "", "hashtags": [], "image_prompt": "",
            "post_variants": [], "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"scores": [{"index": 1, "score": 9, "reason": "very relevant"}, '
                    '{"index": 2, "score": 8, "reason": "relevant"}, '
                    '{"index": 3, "score": 2, "reason": "not relevant"}], '
                    '"best_index": 1, "best_reason": "most relevant to fintech"}'
        )

        with patch("app.agents.social_media.get_llm", return_value=mock_llm):
            result = await score_trends_node(state)

        assert "scored_trends" in result
        assert "selected_trend" in result
        assert result["selected_trend"]["title"] == MOCK_TRENDS[0]["title"]

    @pytest.mark.asyncio
    async def test_no_trends_returns_empty(self):
        from app.agents.social_media import score_trends_node, SocialAgentState

        state: SocialAgentState = {
            "user_id": "u1", "social_links": [], "niche": "fintech",
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "", "raw_profiles": [], "user_profile": {},
            "detected_niche": "fintech", "raw_trends": [], "scored_trends": [],
            "selected_trend": {}, "draft_post": "", "refined_post": "",
            "hashtags": [], "image_prompt": "", "post_variants": [],
            "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }

        result = await score_trends_node(state)
        assert result["scored_trends"] == []
        assert result["selected_trend"] == {}


# ── Unit: refine_post node ────────────────────────────────────────────────────

class TestRefinePostNode:

    @pytest.mark.asyncio
    async def test_returns_refined_post(self):
        from app.agents.social_media import refine_post_node, SocialAgentState

        state: SocialAgentState = {
            "user_id": "u1", "social_links": [], "niche": "fintech",
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "", "raw_profiles": [], "user_profile": {},
            "detected_niche": "fintech", "raw_trends": [], "scored_trends": [],
            "selected_trend": {}, "draft_post": SAMPLE_LINKEDIN_POST.strip(),
            "refined_post": "", "hashtags": [], "image_prompt": "", "post_variants": [],
            "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"hook_score": 9, "authenticity_score": 8, "engagement_score": 9, '
                    '"platform_fit_score": 9, "overall_score": 0.875, "needs_revision": false, '
                    '"feedback": "Strong hook and engagement driver. No changes needed.", '
                    '"refined_post": "' + SAMPLE_LINKEDIN_POST.strip().replace('"', '\\"').replace("\n", "\\n") + '"}'
        )

        with patch("app.agents.social_media.get_llm", return_value=mock_llm):
            result = await refine_post_node(state)

        assert "refined_post" in result
        assert result["quality_score"] == 0.875
        assert result["quality_feedback"] != ""

    @pytest.mark.asyncio
    async def test_empty_draft_handled_gracefully(self):
        from app.agents.social_media import refine_post_node, SocialAgentState

        state: SocialAgentState = {
            "user_id": "u1", "social_links": [], "niche": "fintech",
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "", "raw_profiles": [], "user_profile": {},
            "detected_niche": "fintech", "raw_trends": [], "scored_trends": [],
            "selected_trend": {}, "draft_post": "",
            "refined_post": "", "hashtags": [], "image_prompt": "", "post_variants": [],
            "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }

        result = await refine_post_node(state)
        assert result["refined_post"] == ""
        assert result["quality_score"] == 0.0


# ── Integration: Full pipeline flow (all nodes, all mocked) ──────────────────

class TestFullPipelineFlow:

    @pytest.mark.asyncio
    async def test_full_pipeline_state_propagation(self):
        """
        Verifies that state updates propagate correctly through all 6 nodes
        without real API calls.
        """
        from app.agents.social_media import (
            SocialAgentState,
            analyze_user_node,
            enrich_profile_node,
            fetch_trends_node,
            score_trends_node,
            compose_post_node,
            refine_post_node,
        )

        state: SocialAgentState = {
            "user_id": "user-test", "social_links": [],
            "niche": "AI startup founder",
            "post_platform": "linkedin", "post_tone": "professional",
            "custom_instructions": "Focus on product launches",
            "raw_profiles": [], "user_profile": {}, "detected_niche": "",
            "raw_trends": [], "scored_trends": [], "selected_trend": {},
            "draft_post": "", "refined_post": "", "hashtags": [],
            "image_prompt": "", "post_variants": [],
            "quality_score": 0.0, "quality_feedback": "", "messages": [],
        }

        mock_llm = AsyncMock()

        # Profile enrichment response
        profile_response = MagicMock(content=json.dumps({
            "niche": "AI startup founder", "style": "professional",
            "audience": "tech founders and investors",
            "topics": ["AI", "startups", "product"], "voice_samples": [],
            "content_gaps": [], "confidence": "medium"
        }))

        # Trend scoring response
        scoring_response = MagicMock(content=json.dumps({
            "scores": [{"index": 1, "score": 8, "reason": "relevant"}],
            "best_index": 1, "best_reason": "most relevant"
        }))

        # Post composition response
        post_response = MagicMock(content=json.dumps({
            "post": "AI is transforming how startups launch products.\n\nHere's what changed.",
            "variants": {"A_personal_story": "story...", "B_data_insight": "data...", "C_contrarian": "hot take..."},
            "hashtags": ["#AI", "#Startup", "#ProductLaunch"],
            "image_prompt": "Futuristic tech office with holographic displays"
        }))

        # Refinement response
        refine_response = MagicMock(content=json.dumps({
            "hook_score": 8, "authenticity_score": 8, "engagement_score": 8,
            "platform_fit_score": 9, "overall_score": 0.82,
            "needs_revision": False, "feedback": "Good post, no changes needed.",
            "refined_post": "AI is transforming how startups launch products.\n\nHere's what changed."
        }))

        mock_llm.ainvoke.side_effect = [
            profile_response, scoring_response, post_response, refine_response
        ]

        with patch("app.agents.social_media.get_llm", return_value=mock_llm), \
             patch("app.services.web_scraper.fetch_trending_news",
                   new=AsyncMock(return_value=MOCK_TRENDS)):

            state.update(await analyze_user_node(state))
            state.update(await enrich_profile_node(state))
            state.update(await fetch_trends_node(state))
            state.update(await score_trends_node(state))
            state.update(await compose_post_node(state))
            state.update(await refine_post_node(state))

        # Verify end state
        assert state["detected_niche"] == "AI startup founder"
        assert len(state["scored_trends"]) > 0
        assert state["draft_post"] != ""
        assert state["refined_post"] != ""
        assert len(state["hashtags"]) > 0
        assert state["image_prompt"] != ""
        assert len(state["post_variants"]) == 3
        assert state["quality_score"] == 0.82


# ── Schema validation tests ───────────────────────────────────────────────────

class TestSchemas:

    def test_compose_response_schema(self):
        from app.schemas.social import SocialComposeResponse, UserProfile, TrendItem

        resp = SocialComposeResponse(
            post="Test post", platform="linkedin",
            hashtags=["#test"], image_prompt="test image",
            quality_score=0.85, quality_feedback="Good",
            variants=["v1", "v2", "v3"],
            selected_trend={}, top_trends=[],
            detected_niche="fintech",
            user_profile=UserProfile(niche="fintech", style="professional",
                                      audience="founders"),
            post_id=None,
        )
        assert resp.post == "Test post"
        assert resp.quality_score == 0.85

    def test_trends_response_schema(self):
        from app.schemas.social import TrendsResponse, TrendItem

        resp = TrendsResponse(
            niche="fintech",
            count=2,
            trends=[
                TrendItem(title="Test trend", summary="Summary", url="", relevance_score=0.8),
            ],
        )
        assert resp.niche == "fintech"
        assert len(resp.trends) == 1

    def test_post_history_item_from_orm(self):
        """Verifies from_attributes mode works with ORM objects."""
        from app.schemas.social import PostHistoryItem
        from datetime import datetime, timezone
        import uuid

        class FakePost:
            id = uuid.uuid4()
            platform = "linkedin"
            post_text = "Test post"
            hashtags = ["#test"]
            niche = "fintech"
            trend_title = "Test trend"
            quality_score = 0.9
            status = "draft"
            created_at = datetime.now(timezone.utc)

        item = PostHistoryItem.model_validate(FakePost())
        assert item.platform == "linkedin"
        assert item.status == "draft"
