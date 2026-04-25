"""
app/utils/chunking.py
=====================
Chunking strategy selector.
Chooses the best chunking approach based on document characteristics.
Used by app/services/vector_store.py.

Strategies:
  - sentence   : SentenceSplitter — best for structured docs (reports, manuals)
  - semantic   : SemanticChunker — best for unstructured prose (articles, emails)
  - hierarchical: Multi-level chunks — best for parent-document retrieval

The vertical also influences strategy:
  - finance / legal: sentence (preserve paragraph integrity)
  - healthcare: semantic (clinical language is dense)
  - manufacturing: sentence (SOPs have numbered steps)
"""

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

VERTICAL_STRATEGIES: dict[str, str] = {
    "finance": "sentence",
    "healthcare": "semantic",
    "manufacturing": "sentence",
    "retail": "sentence",
    "logistics": "sentence",
}


def select_chunking_strategy(
    text: str,
    vertical: str | None = None,
    force: str | None = None,
) -> str:
    """
    Return the recommended chunking strategy name for the given document.

    Args:
        text: Document text content
        vertical: SME vertical (finance | healthcare | manufacturing | retail | logistics)
        force: Override strategy (sentence | semantic | hierarchical)

    Returns:
        Strategy name: 'sentence' | 'semantic' | 'hierarchical'
    """
    if force:
        return force

    if vertical and vertical in VERTICAL_STRATEGIES:
        strategy = VERTICAL_STRATEGIES[vertical]
        log.debug("chunking strategy from vertical", vertical=vertical, strategy=strategy)
        return strategy

    # Heuristic: long, dense prose → semantic; short/structured → sentence
    avg_sentence_len = len(text) / max(text.count("."), 1)

    if len(text) > 10_000 and avg_sentence_len > 80:
        return "semantic"

    return "sentence"
