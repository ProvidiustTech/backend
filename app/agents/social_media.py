"""
app/agents/social_media.py
===========================
Production Social Media AI Agent — LangGraph pipeline.

Implements everything from your notes (Image 2):

  i.  Trending news checker for user's specific niche
      (a) Profile analysis via provided social links
          — extracts writing style, audience, recurring topics
      (b) Cross-platform interest mapping from profile text
          — finds topic clusters without needing platform APIs
  ii. Post composer tailored to user's niche, style, and platform
      — includes hashtag generation and image prompt

LangGraph nodes:
  analyze_user
      ↓
  enrich_profile      ← new: deeper topic + style extraction using LLM
      ↓
  fetch_trends
      ↓
  score_trends        ← new: ranks trends by relevance to THIS user
      ↓
  compose_post
      ↓
  refine_post         ← new: self-critique pass for quality control
      ↓
  END

All nodes are independently testable (pure async functions on state dict).
"""

import json
import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.core.logging import get_logger
from app.services.llm import get_llm

log = get_logger(__name__)


# ── State ──────────────────────────────────────────────────────────────────────

class SocialAgentState(TypedDict):
    # ── Input ──────────────────────────────────────────────────────────────────
    user_id: str
    social_links: list[str]         # public profile URLs on any platform
    niche: str | None               # explicit override; auto-detected if empty
    post_platform: str              # twitter | linkedin | instagram | threads | facebook
    post_tone: str                  # professional | casual | witty | inspirational | educational
    custom_instructions: str        # optional free-text instructions from user

    # ── Profile analysis ───────────────────────────────────────────────────────
    raw_profiles: list[dict]        # raw scraped profile data per link
    user_profile: dict              # synthesised: niche, style, audience, topics, voice_samples
    detected_niche: str             # final resolved niche string

    # ── Trend pipeline ─────────────────────────────────────────────────────────
    raw_trends: list[dict]          # all fetched articles before scoring
    scored_trends: list[dict]       # ranked by relevance to user's niche + profile
    selected_trend: dict            # the single trend chosen for the post

    # ── Post generation ────────────────────────────────────────────────────────
    draft_post: str
    refined_post: str               # after self-critique pass
    hashtags: list[str]
    image_prompt: str               # detailed image description for DALL-E or Midjourney
    post_variants: list[str]        # 3 alternative angles on same topic
    quality_score: float            # 0-1 self-assessed quality
    quality_feedback: str           # what the refinement changed

    # ── Conversation ───────────────────────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_json(text: str, fallback: Any = None) -> Any:
    """Parse JSON from LLM response, stripping markdown fences."""
    cleaned = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object/array within the text
        obj_m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        arr_m = re.search(r"\[.*\]", cleaned, re.DOTALL)
        for m in [obj_m, arr_m]:
            if m:
                try:
                    return json.loads(m.group())
                except Exception:
                    pass
        log.warning("JSON parse failed, using fallback", raw=cleaned[:200])
        return fallback


_PLATFORM_RULES: dict[str, dict] = {
    "twitter": {
        "char_limit": 280,
        "style": "Ultra-concise. One punchy idea. Ends with a hook or question.",
        "format": "Plain text. Max 2-3 hashtags at end. No emojis unless they add meaning.",
        "hook_style": "Bold statement or contrarian take",
    },
    "linkedin": {
        "char_limit": 3000,
        "style": "Professional but personal. 3-5 short paragraphs. White space is your friend.",
        "format": "Start with a scroll-stopping first line. Use line breaks. 3-5 hashtags at end.",
        "hook_style": "Personal story or surprising statistic",
    },
    "instagram": {
        "char_limit": 2200,
        "style": "Visual storytelling. Conversational, warm. Inspires saves and shares.",
        "format": "Short punchy sentences. Heavy emoji use is fine. 10-20 hashtags in first comment or caption end.",
        "hook_style": "Relatable scenario or aspirational statement",
    },
    "threads": {
        "char_limit": 500,
        "style": "Casual, direct, like a text message to a smart friend.",
        "format": "1-3 short paragraphs. 0-2 hashtags. Conversational.",
        "hook_style": "Honest opinion or real observation",
    },
    "facebook": {
        "char_limit": 2000,
        "style": "Community-focused. Asks for engagement. Tells a complete story.",
        "format": "Longer form is fine. Use clear paragraphs. 1-3 hashtags.",
        "hook_style": "Question or shared experience",
    },
}


# ── Node 1: analyze_user ───────────────────────────────────────────────────────

async def analyze_user_node(state: SocialAgentState) -> dict:
    """
    (a) Scrape each provided social link.
    (b) Collect raw profile data across platforms.

    Does NOT do LLM synthesis here — that's enrich_profile_node.
    Keeping IO and computation in separate nodes makes retries cleaner.
    """
    from app.services.web_scraper import scrape_social_profile

    links = state.get("social_links", [])
    raw_profiles: list[dict] = []

    # Scrape all links concurrently
    import asyncio
    tasks = [scrape_social_profile(link) for link in links]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for link, result in zip(links, results):
        if isinstance(result, dict) and result:
            raw_profiles.append(result)
            log.info("profile scraped", platform=result.get("platform"), username=result.get("username"))
        elif isinstance(result, Exception):
            log.warning("profile scrape error", url=link[:60], error=str(result))

    log.info("analyze_user complete", profiles_scraped=len(raw_profiles))
    return {"raw_profiles": raw_profiles}


# ── Node 2: enrich_profile ─────────────────────────────────────────────────────

async def enrich_profile_node(state: SocialAgentState) -> dict:
    """
    Use LLM to synthesise scraped profile data into a rich user profile.

    Extracts:
    - Confirmed niche (with confidence reasoning)
    - Writing style (tone, vocabulary level, sentence length preference)
    - Target audience (who they speak to)
    - Top recurring topics
    - Voice samples (characteristic phrases / sentence patterns)
    - Content gaps (what they haven't posted about but should)
    """
    raw_profiles = state.get("raw_profiles", [])
    explicit_niche = state.get("niche")

    # If no profiles scraped and no explicit niche, use minimal fallback
    if not raw_profiles and not explicit_niche:
        fallback = {
            "niche": "business professional",
            "style": state.get("post_tone", "professional"),
            "audience": "professionals and entrepreneurs",
            "topics": ["business", "growth", "strategy"],
            "voice_samples": [],
            "content_gaps": [],
            "confidence": "low",
        }
        return {"user_profile": fallback, "detected_niche": fallback["niche"]}

    llm = get_llm(temperature=0.2)

    # Build profile summary for LLM
    profile_summaries = []
    for p in raw_profiles:
        summary = (
            f"Platform: {p.get('platform', 'unknown')}\n"
            f"Username: {p.get('username', 'unknown')}\n"
            f"Bio: {p.get('bio', 'N/A')}\n"
            f"Page title: {p.get('page_title', 'N/A')}\n"
            f"Topic hints from text: {', '.join(p.get('topics_hint', []))}\n"
            f"Snippet: {p.get('raw_snippet', '')[:400]}"
        )
        profile_summaries.append(summary)

    combined = "\n\n---\n\n".join(profile_summaries)
    explicit_niche_line = f'The user says their niche is: "{explicit_niche}"' if explicit_niche else ""

    prompt = f"""You are a social media strategist analysing a creator's profile.
{explicit_niche_line}

Profile data scraped from their social links:
{combined[:4000]}

Analyse this data and extract a structured creator profile.

Respond ONLY with valid JSON in this exact format:
{{
  "niche": "2-4 word niche description",
  "style": "one of: professional | casual | technical | inspirational | educational | witty",
  "audience": "description of who they speak to",
  "topics": ["topic1", "topic2", "topic3", "topic4", "topic5"],
  "voice_samples": ["characteristic phrase or sentence pattern"],
  "content_gaps": ["topic they should post about but haven't"],
  "confidence": "high | medium | low"
}}"""

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    profile = _safe_json(response.content, fallback={
        "niche": explicit_niche or "business",
        "style": state.get("post_tone", "professional"),
        "audience": "professionals",
        "topics": [explicit_niche or "business"],
        "voice_samples": [],
        "content_gaps": [],
        "confidence": "low",
    })

    # Explicit niche override always wins
    if explicit_niche:
        profile["niche"] = explicit_niche

    detected_niche = profile.get("niche", explicit_niche or "business")
    log.info("profile enriched", niche=detected_niche, confidence=profile.get("confidence"))

    return {"user_profile": profile, "detected_niche": detected_niche}


# ── Node 3: fetch_trends ───────────────────────────────────────────────────────

async def fetch_trends_node(state: SocialAgentState) -> dict:
    """
    Fetch trending articles for the user's detected niche.
    Always fetches more than needed (10) so scoring can pick the best ones.
    """
    from app.services.web_scraper import fetch_trending_news

    niche = state.get("detected_niche") or state.get("niche") or "business"

    # Fetch from multiple angle queries for diversity
    import asyncio
    niche_queries = [niche, f"{niche} trends", f"{niche} news"]
    fetch_tasks = [fetch_trending_news(q, limit=5) for q in niche_queries[:2]]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    all_articles: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_articles.extend(r)

    # Deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for a in all_articles:
        key = re.sub(r"\W+", "", a.get("title", "").lower())[:30]
        if key not in seen and key:
            seen.add(key)
            unique.append(a)

    log.info("trends fetched", niche=niche, raw_count=len(unique))
    return {"raw_trends": unique}


# ── Node 4: score_trends ───────────────────────────────────────────────────────

async def score_trends_node(state: SocialAgentState) -> dict:
    """
    Rank raw trends by relevance to THIS specific user's profile and niche.
    Also picks the single best trend to build the post around.

    Uses LLM for relevance scoring — more accurate than keyword overlap.
    Falls back to the first trend if LLM scoring fails.
    """
    trends = state.get("raw_trends", [])
    profile = state.get("user_profile", {})
    niche = state.get("detected_niche", "business")

    if not trends:
        # No trends found — LLM will generate from knowledge
        log.warning("no trends to score, proceeding without trend context")
        return {"scored_trends": [], "selected_trend": {}}

    if len(trends) == 1:
        return {"scored_trends": trends, "selected_trend": trends[0]}

    llm = get_llm(temperature=0.1)

    trend_list = "\n".join([
        f"{i+1}. {t.get('title', '')} — {t.get('summary', '')[:100]}"
        for i, t in enumerate(trends[:8])
    ])

    prompt = f"""You are selecting the best trending topic for a social media post.

Creator profile:
- Niche: {niche}
- Audience: {profile.get('audience', 'professionals')}
- Style: {profile.get('style', 'professional')}
- Topics they cover: {', '.join(profile.get('topics', [])[:5])}

Trending articles (numbered):
{trend_list}

For each trend give a relevance score 0-10 for this creator. Then pick the single best one.

Respond ONLY with valid JSON:
{{
  "scores": [
    {{"index": 1, "score": 8, "reason": "directly relevant because..."}},
    ...
  ],
  "best_index": 2,
  "best_reason": "why this one makes the best post"
}}"""

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    scoring = _safe_json(response.content)

    if not scoring or "scores" not in scoring:
        log.warning("trend scoring failed, using first trend")
        return {"scored_trends": trends, "selected_trend": trends[0]}

    # Apply scores to trends
    score_map = {s["index"] - 1: s["score"] for s in scoring.get("scores", [])}
    for i, t in enumerate(trends[:8]):
        t["relevance_score"] = score_map.get(i, 0.5)

    scored = sorted(trends[:8], key=lambda x: x.get("relevance_score", 0), reverse=True)
    best_idx = scoring.get("best_index", 1) - 1
    selected = trends[min(best_idx, len(trends) - 1)]

    log.info(
        "trends scored",
        total=len(scored),
        selected=selected.get("title", "")[:60],
        reason=scoring.get("best_reason", ""),
    )

    return {"scored_trends": scored, "selected_trend": selected}


# ── Node 5: compose_post ──────────────────────────────────────────────────────

async def compose_post_node(state: SocialAgentState) -> dict:
    """
    ii. Compose a social media post tailored to:
    - User's niche and writing voice
    - The selected trending topic
    - Platform-specific rules
    - Custom instructions if provided

    Also generates:
    - 3 content angle variants (same trend, different perspectives)
    - Hashtag set optimised for reach in this niche
    - Detailed image prompt for AI image generation
    """
    llm = get_llm(temperature=0.75)

    platform = state.get("post_platform", "linkedin")
    profile = state.get("user_profile", {})
    selected = state.get("selected_trend", {})
    niche = state.get("detected_niche", "business")
    custom = state.get("custom_instructions", "")
    rules = _PLATFORM_RULES.get(platform, _PLATFORM_RULES["linkedin"])

    # Build trend context
    if selected:
        trend_context = (
            f"Trending topic: {selected.get('title', '')}\n"
            f"Summary: {selected.get('summary', '')[:200]}\n"
            f"Source: {selected.get('source', 'news')}"
        )
    else:
        trend_context = f"No specific trend — write about a timely topic in {niche}."

    voice_samples = profile.get("voice_samples", [])
    voice_text = (
        f"Voice samples (their typical phrases/patterns):\n" + "\n".join(f'- "{v}"' for v in voice_samples[:3])
        if voice_samples else ""
    )

    prompt = f"""You are a ghostwriter for a {profile.get('niche', niche)} creator on {platform}.

CREATOR PROFILE:
- Niche: {profile.get('niche', niche)}
- Writing style: {profile.get('style', state.get('post_tone', 'professional'))}
- Target audience: {profile.get('audience', 'professionals')}
- Recurring topics: {', '.join(profile.get('topics', [])[:5])}
{voice_text}

PLATFORM: {platform.upper()}
- Character limit: {rules['char_limit']}
- Style guide: {rules['style']}
- Format: {rules['format']}
- Hook style: {rules['hook_style']}

TREND TO REFERENCE:
{trend_context}

{f"CUSTOM INSTRUCTIONS: {custom}" if custom else ""}

TASK:
1. Write ONE primary post. It must:
   - Open with a scroll-stopping hook (no "I" as the first word)
   - Reference the trend naturally, not forcefully
   - Add the creator's unique angle/insight
   - End with engagement driver (question, call to action, bold claim)
   - Stay within {rules['char_limit']} characters
   - Sound human, not AI-generated

2. Write 3 content VARIANTS (same trend, different angles):
   - Variant A: Personal story angle
   - Variant B: Data / insight angle
   - Variant C: Contrarian / hot-take angle

3. Generate 8-12 relevant hashtags (mix of niche, broad, and trending)

4. Describe an image for this post (detailed, for AI image generation)

Respond ONLY with valid JSON:
{{
  "post": "...",
  "variants": {{
    "A_personal_story": "...",
    "B_data_insight": "...",
    "C_contrarian": "..."
  }},
  "hashtags": ["#tag1", ...],
  "image_prompt": "Detailed visual description..."
}}"""

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    result = _safe_json(response.content)

    if not result or "post" not in result:
        # Hard fallback
        draft = response.content[:rules["char_limit"]]
        result = {
            "post": draft,
            "variants": {"A_personal_story": draft, "B_data_insight": draft, "C_contrarian": draft},
            "hashtags": [f"#{niche.replace(' ', '')}"],
            "image_prompt": f"Professional image for {niche} content",
        }

    variants_dict = result.get("variants", {})
    post_variants = list(variants_dict.values()) if isinstance(variants_dict, dict) else []

    log.info(
        "post composed",
        platform=platform,
        post_len=len(result.get("post", "")),
        hashtags=len(result.get("hashtags", [])),
        variants=len(post_variants),
    )

    return {
        "draft_post": result["post"],
        "post_variants": post_variants,
        "hashtags": result.get("hashtags", []),
        "image_prompt": result.get("image_prompt", ""),
        "messages": [AIMessage(content=result["post"])],
    }


# ── Node 6: refine_post ───────────────────────────────────────────────────────

async def refine_post_node(state: SocialAgentState) -> dict:
    """
    Self-critique pass on the draft post.

    The LLM reviews its own output against:
    - Platform rules
    - Authenticity (does it sound human?)
    - Engagement potential
    - Character limit compliance

    If quality score >= 0.8, returns as-is.
    If < 0.8, rewrites the weakest elements.
    """
    draft = state.get("draft_post", "")
    platform = state.get("post_platform", "linkedin")
    rules = _PLATFORM_RULES.get(platform, _PLATFORM_RULES["linkedin"])

    if not draft:
        return {"refined_post": "", "quality_score": 0.0, "quality_feedback": "No draft to refine"}

    llm = get_llm(temperature=0.3)

    prompt = f"""You are a senior social media editor reviewing a {platform} post.

DRAFT POST:
---
{draft}
---

PLATFORM RULES:
- Char limit: {rules['char_limit']} (current: {len(draft)})
- Style: {rules['style']}
- Hook style: {rules['hook_style']}

Evaluate this post on:
1. Hook strength (0-10): Does the opening line stop the scroll?
2. Authenticity (0-10): Does it sound human and credible?
3. Engagement potential (0-10): Will people comment/share/like?
4. Platform fit (0-10): Does it follow {platform}'s norms?

Then rewrite ONLY if score < 8/10 average. Fix the weakest element.

Respond ONLY with valid JSON:
{{
  "hook_score": 8,
  "authenticity_score": 7,
  "engagement_score": 8,
  "platform_fit_score": 9,
  "overall_score": 0.8,
  "needs_revision": false,
  "feedback": "What you changed or why it's good",
  "refined_post": "The improved version (or original if no changes needed)"
}}"""

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    review = _safe_json(response.content)

    if not review:
        return {
            "refined_post": draft,
            "quality_score": 0.75,
            "quality_feedback": "Refinement parse failed, using original draft",
        }

    refined = review.get("refined_post") or draft
    score = float(review.get("overall_score", 0.75))
    feedback = review.get("feedback", "")

    log.info(
        "post refined",
        quality_score=score,
        was_revised=review.get("needs_revision", False),
        feedback=feedback[:100],
    )

    return {
        "refined_post": refined,
        "quality_score": score,
        "quality_feedback": feedback,
    }


# ── Graph Builder ──────────────────────────────────────────────────────────────

def build_social_agent() -> StateGraph:
    """
    Builds and compiles the Social Media Agent LangGraph.

    Full pipeline:
      START
        → analyze_user       (scrape profile URLs concurrently)
        → enrich_profile     (LLM synthesis of profile data)
        → fetch_trends       (RSS + DDG for niche news)
        → score_trends       (LLM ranks trends by user relevance)
        → compose_post       (platform-aware post generation)
        → refine_post        (self-critique quality pass)
        → END

    To add future nodes (e.g. schedule_post, A/B_test):
      graph.add_node("schedule", schedule_node)
      graph.add_edge("refine_post", "schedule")
      graph.add_edge("schedule", END)
    """
    graph = StateGraph(SocialAgentState)

    graph.add_node("analyze_user",    analyze_user_node)
    graph.add_node("enrich_profile",  enrich_profile_node)
    graph.add_node("fetch_trends",    fetch_trends_node)
    graph.add_node("score_trends",    score_trends_node)
    graph.add_node("compose_post",    compose_post_node)
    graph.add_node("refine_post",     refine_post_node)

    graph.add_edge(START,           "analyze_user")
    graph.add_edge("analyze_user",  "enrich_profile")
    graph.add_edge("enrich_profile","fetch_trends")
    graph.add_edge("fetch_trends",  "score_trends")
    graph.add_edge("score_trends",  "compose_post")
    graph.add_edge("compose_post",  "refine_post")
    graph.add_edge("refine_post",   END)

    return graph.compile()


social_agent = build_social_agent()
