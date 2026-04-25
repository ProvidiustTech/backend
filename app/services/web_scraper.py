"""
app/services/web_scraper.py
============================
Production-grade async web scraping service.

PRIMARY ROLE: scrape_company_context()
  This is the main data source for the Customer Service Agent.
  Given a company URL, it discovers and scrapes:
    - Main/home page
    - About / About Us page
    - FAQ / Help / Support page
    - Pricing page (if found)
    - Contact page
    - Sitemap (if available) for additional page discovery

SECONDARY ROLE: fetch_trending_news() + scrape_social_profile()
  Used by the Social Media Agent (secondary feature).

Architecture:
  - httpx async client with retry (tenacity)
  - Rotating User-Agent pool
  - Sitemap-aware page discovery
  - TTL in-process cache (24h for company pages, 60min for trends)
  - Never raises — all errors are caught and logged

Caching strategy:
  Company scrapes are expensive (multiple pages, 5-10s).
  We cache the full result in memory AND persist to DB via CompanyRegistration.
  The DB cache survives server restarts; memory cache avoids DB reads.
"""

import asyncio
import hashlib
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import get_logger

log = get_logger(__name__)

# ── User-Agent rotation ───────────────────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]
_ua_idx = 0

def _next_ua() -> str:
    global _ua_idx
    ua = _USER_AGENTS[_ua_idx % len(_USER_AGENTS)]
    _ua_idx += 1
    return ua

def _headers(extra: dict | None = None) -> dict:
    h = {
        "User-Agent": _next_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
    }
    if extra:
        h.update(extra)
    return h


# ── TTL cache ─────────────────────────────────────────────────────────────────
_cache: dict[str, tuple[Any, float]] = {}

def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None

def _cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    _cache[key] = (value, time.time() + ttl)

def _ck(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()


# ── HTML extraction ───────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """
    Strips HTML to clean readable text.
    Skips nav/header/footer/script/style entirely.
    Preserves paragraph structure.
    """
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg",
            "iframe", "form", "button", "input", "select", "textarea"}
    BREAK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li",
             "article", "section", "tr", "br", "blockquote"}

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._depth += 1
        elif self._depth == 0 and tag in self.BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._depth > 0:
            self._depth -= 1

    def handle_data(self, data):
        if self._depth == 0:
            t = data.strip()
            if len(t) > 2:
                self._parts.append(t)

    def text(self) -> str:
        raw = " ".join(self._parts)
        raw = re.sub(r"[ \t]{2,}", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _extract_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.text()


# ── Core HTTP fetcher ─────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    reraise=False,
)
async def _fetch(url: str, timeout: int = 12, extra: dict | None = None) -> str | None:
    try:
        async with httpx.AsyncClient(
            headers=_headers(extra),
            follow_redirects=True,
            timeout=timeout,
            verify=True,
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.text
    except httpx.HTTPStatusError as e:
        log.warning("HTTP error", url=url[:80], status=e.response.status_code)
        return None
    except Exception as e:
        log.warning("fetch failed", url=url[:80], error=type(e).__name__)
        return None


def _base(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"

def _resolve(href: str, base_url: str) -> str | None:
    """Resolve a potentially relative href to an absolute URL on the same domain."""
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    full = urljoin(base_url, href)
    # Only follow links on the same domain
    if urlparse(full).netloc != urlparse(base_url).netloc:
        return None
    return full


# ══════════════════════════════════════════════════════════════════════════════
# PRIMARY: Company context scraping (Customer Service Agent)
# ══════════════════════════════════════════════════════════════════════════════

# Page types to discover and their URL patterns
_PAGE_PATTERNS: dict[str, list[str]] = {
    "about":   [r"/about", r"/about-us", r"/who-we-are", r"/company", r"/our-story"],
    "faq":     [r"/faq", r"/faqs", r"/help", r"/support", r"/questions", r"/knowledge"],
    "pricing": [r"/pricing", r"/plans", r"/prices", r"/cost"],
    "contact": [r"/contact", r"/contact-us", r"/get-in-touch", r"/reach-us"],
}


def _discover_links(html: str, base_url: str) -> dict[str, str]:
    """
    Scan the main page HTML for links matching our target page types.
    Returns {page_type: absolute_url}.
    """
    # Extract all href values
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    found: dict[str, str] = {}

    for href in hrefs:
        resolved = _resolve(href, base_url)
        if not resolved:
            continue
        path = urlparse(resolved).path.lower()
        for page_type, patterns in _PAGE_PATTERNS.items():
            if page_type not in found:
                if any(re.search(pat, path) for pat in patterns):
                    found[page_type] = resolved

    return found


async def _parse_sitemap(base_url: str) -> list[str]:
    """
    Try to fetch /sitemap.xml and extract useful page URLs.
    Returns up to 20 non-image, non-feed URLs from the same domain.
    """
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    xml = await _fetch(sitemap_url, timeout=8)
    if not xml:
        return []

    urls = re.findall(r"<loc>(https?://[^<]+)</loc>", xml)
    base_domain = urlparse(base_url).netloc

    useful = []
    for u in urls:
        if urlparse(u).netloc != base_domain:
            continue
        # Skip images, feeds, tags, category pages
        if re.search(r"\.(jpg|jpeg|png|gif|pdf|xml|rss)|/tag/|/category/|/page/\d", u, re.I):
            continue
        useful.append(u)

    return useful[:20]


class ScrapeResult:
    """Result of scraping a single page."""
    def __init__(self, url: str, page_type: str, text: str):
        self.url = url
        self.page_type = page_type
        self.text = text
        self.char_count = len(text)


async def scrape_company_context(
    url: str,
    company_id: str,
    max_pages: int = 5,
    force_refresh: bool = False,
) -> tuple[str, list[ScrapeResult]]:
    """
    PRIMARY FUNCTION — Customer Service Agent data source.

    Scrapes a company website to build a knowledge base for the CS agent.

    Strategy:
      1. Check in-memory cache (24h TTL)
      2. Fetch the main URL
      3. Discover linked pages (About, FAQ, Pricing, Contact) from main page
      4. Try sitemap.xml for additional page discovery
      5. Scrape discovered pages concurrently (max_pages limit)
      6. Combine and return structured text

    Args:
        url:           Company homepage or any public page
        company_id:    Used for cache key and logging
        max_pages:     Max pages to scrape (default 5 to stay polite)
        force_refresh: Bypass cache

    Returns:
        Tuple of (combined_text: str, scrape_results: list[ScrapeResult])
        combined_text is ready to inject into the agent's system prompt.
    """
    cache_key = _ck("company_context", url)

    if not force_refresh:
        cached = _cache_get(cache_key)
        if cached:
            log.debug("company context cache hit", company_id=company_id)
            return cached

    log.info("scraping company context", url=url[:80], company_id=company_id)
    base = _base(url)
    results: list[ScrapeResult] = []

    # ── Step 1: Fetch main page ───────────────────────────────────────────────
    main_html = await _fetch(url)
    if not main_html:
        log.error("failed to fetch main page", url=url[:80])
        return "", []

    main_text = _extract_text(main_html)
    results.append(ScrapeResult(url=url, page_type="main", text=main_text))

    # ── Step 2: Discover linked pages ─────────────────────────────────────────
    discovered = _discover_links(main_html, base)
    log.info("pages discovered", company_id=company_id, pages=list(discovered.keys()))

    # ── Step 3: Try sitemap for additional discovery ──────────────────────────
    sitemap_urls = await _parse_sitemap(base)
    # Fill any gaps from sitemap
    for page_type, patterns in _PAGE_PATTERNS.items():
        if page_type not in discovered:
            for su in sitemap_urls:
                path = urlparse(su).path.lower()
                if any(re.search(pat, path) for pat in patterns):
                    discovered[page_type] = su
                    break

    # ── Step 4: Scrape discovered pages concurrently ─────────────────────────
    pages_to_scrape = list(discovered.items())[:max_pages - 1]  # -1 for main

    async def _scrape_page(page_type: str, page_url: str) -> ScrapeResult | None:
        html = await _fetch(page_url, timeout=10)
        if not html:
            return None
        text = _extract_text(html)
        if len(text) < 50:  # skip empty/JS-only pages
            return None
        return ScrapeResult(url=page_url, page_type=page_type, text=text)

    scrape_tasks = [_scrape_page(pt, pu) for pt, pu in pages_to_scrape]
    page_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)

    for r in page_results:
        if isinstance(r, ScrapeResult):
            results.append(r)
            log.info("page scraped", page_type=r.page_type, chars=r.char_count)

    # ── Step 5: Build combined text ───────────────────────────────────────────
    # Each section is labelled so the LLM knows which part is FAQ vs About etc.
    # Per-section limit keeps the context window manageable.
    section_limits = {
        "main":    1500,
        "about":   2000,
        "faq":     3000,   # FAQ gets the most space — most useful for CS
        "pricing": 1500,
        "contact": 800,
    }

    sections = []
    for r in results:
        limit = section_limits.get(r.page_type, 1200)
        text = r.text[:limit]
        if text.strip():
            label = r.page_type.upper().replace("_", " ")
            sections.append(f"[{label} — {r.url}]\n{text}")

    combined = "\n\n---\n\n".join(sections)

    # Cache for 24 hours
    output = (combined, results)
    _cache_set(cache_key, output, ttl=86400)

    log.info(
        "company scrape complete",
        company_id=company_id,
        pages=len(results),
        total_chars=len(combined),
    )
    return output


# ══════════════════════════════════════════════════════════════════════════════
# SECONDARY: Social Media Agent helpers
# ══════════════════════════════════════════════════════════════════════════════

# RSS feeds mapped by niche for trend fetching
_NICHE_RSS: dict[str, list[str]] = {
    "technology":    ["https://feeds.feedburner.com/TechCrunch", "https://www.theverge.com/rss/index.xml"],
    "ai":            ["https://www.artificialintelligence-news.com/feed/", "https://feeds.feedburner.com/TechCrunch"],
    "fintech":       ["https://www.finextra.com/rss/headlines.aspx", "https://techcrunch.com/category/fintech/feed/"],
    "finance":       ["https://www.finextra.com/rss/headlines.aspx"],
    "crypto":        ["https://cointelegraph.com/rss", "https://decrypt.co/feed"],
    "startup":       ["https://feeds.feedburner.com/TechCrunch"],
    "marketing":     ["https://searchengineland.com/feed"],
    "health":        ["https://rss.medicalnewstoday.com/featurednews.xml"],
    "business":      ["https://www.fastcompany.com/latest/rss"],
    "retail":        ["https://www.retaildive.com/feeds/news/"],
    "logistics":     ["https://www.supplychaindive.com/feeds/news/"],
    "manufacturing": ["https://www.industryweek.com/rss"],
    "default":       ["https://feeds.feedburner.com/TechCrunch"],
}

def _rss_for_niche(niche: str) -> list[str]:
    nl = niche.lower()
    for key, feeds in _NICHE_RSS.items():
        if key in nl or nl in key:
            return feeds
    if any(w in nl for w in ["tech", "software", "saas", "dev"]):
        return _NICHE_RSS["technology"]
    if any(w in nl for w in ["invest", "bank", "money", "fund"]):
        return _NICHE_RSS["finance"]
    if any(w in nl for w in ["brand", "seo", "content", "social"]):
        return _NICHE_RSS["marketing"]
    return _NICHE_RSS["default"]


def _parse_rss(xml: str, limit: int) -> list[dict]:
    articles = []
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    if not items:
        items = re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL)

    for item in items:
        title_m = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.DOTALL)
        desc_m  = re.search(r"<description[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", item, re.DOTALL)
        link_m  = re.search(r"<link[^>]*>\s*(https?://[^\s<]+)", item, re.DOTALL)

        title = re.sub(r"<[^>]+>", "", title_m.group(1) if title_m else "").strip()
        desc  = re.sub(r"<[^>]+>", "", desc_m.group(1)  if desc_m  else "").strip()[:250]
        link  = link_m.group(1).strip() if link_m else ""

        for ent, ch in [("&amp;","&"),("&lt;","<"),("&gt;",">"),("&quot;",'"'),("&#39;","'")]:
            title = title.replace(ent, ch)
            desc  = desc.replace(ent, ch)

        if title and len(title) > 15:
            articles.append({
                "title": title, "summary": desc or title,
                "url": link, "relevance_score": 0.8, "source": "rss",
            })
            if len(articles) >= limit:
                break
    return articles


async def _ddg_search(query: str, limit: int = 5) -> list[dict]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&df=w"
    html = await _fetch(url, extra={"Referer": "https://duckduckgo.com/"})
    if not html:
        return []
    results = []
    titles = re.findall(r'<h2 class="result__title"[^>]*>\s*<a[^>]*>([^<]{20,200})</a>', html)
    for t in titles[:limit]:
        results.append({
            "title": t.strip(), "summary": t.strip(),
            "url": "", "relevance_score": 0.65, "source": "duckduckgo",
        })
    return results


async def fetch_trending_news(niche: str, limit: int = 5) -> list[dict]:
    """Fetch trending news for the Social Media Agent."""
    ck = _ck("trends", niche, str(limit))
    cached = _cache_get(ck)
    if cached:
        return cached

    articles: list[dict] = []
    feeds = _rss_for_niche(niche)
    tasks = [_fetch(f, timeout=8) for f in feeds]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for xml in results:
        if isinstance(xml, str) and xml:
            articles.extend(_parse_rss(xml, limit))
        if len(articles) >= limit:
            break

    if len(articles) < limit:
        ddg = await _ddg_search(f"{niche} news 2026", limit - len(articles))
        articles.extend(ddg)

    # Deduplicate
    seen: set[str] = set()
    unique = []
    for a in articles:
        k = re.sub(r"\W+", "", a["title"].lower())[:40]
        if k not in seen:
            seen.add(k)
            unique.append(a)

    result = unique[:limit]
    _cache_set(ck, result, ttl=3600)
    return result


async def scrape_social_profile(url: str) -> dict | None:
    """Scrape a public social profile for the Social Media Agent."""
    ck = _ck("profile", url)
    cached = _cache_get(ck)
    if cached:
        return cached

    html = await _fetch(url, timeout=12)
    if not html:
        return None

    # OG meta tags
    og: dict = {}
    for key in ["title", "description"]:
        m = re.search(rf'<meta[^>]+property=["\']og:{key}["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            og[key] = m.group(1).strip()

    platform = "unknown"
    for p, kw in [("twitter","twitter.com"), ("twitter","x.com"),
                  ("linkedin","linkedin.com"), ("instagram","instagram.com"),
                  ("facebook","facebook.com"), ("tiktok","tiktok.com")]:
        if kw in url.lower():
            platform = p
            break

    path_parts = [p for p in urlparse(url).path.rstrip("/").split("/") if p and p not in ("in","user","@")]
    username = path_parts[-1] if path_parts else ""

    result = {
        "url": url, "platform": platform, "username": username,
        "bio": og.get("description", "")[:500],
        "page_title": og.get("title", "")[:200],
        "topics_hint": [],
        "og_data": og,
        "raw_snippet": _extract_text(html)[:1500],
    }
    _cache_set(ck, result, ttl=86400)
    return result
