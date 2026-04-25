"""
app/utils/text.py
=================
Shared text preprocessing utilities used by the indexing pipeline.
"""

import re
import unicodedata


def clean_text(text: str) -> str:
    """
    Normalize whitespace, remove control characters, fix unicode.
    Call before chunking any document.
    """
    # Normalize unicode to NFC form
    text = unicodedata.normalize("NFC", text)
    # Remove null bytes and other control chars (except newlines/tabs)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Collapse multiple blank lines into at most two
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces (but not newlines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def truncate_text(text: str, max_chars: int = 500, suffix: str = "...") -> str:
    """Truncate text to max_chars, breaking on word boundary."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + suffix


def estimate_tokens(text: str) -> int:
    """
    Rough token estimate (1 token ≈ 4 characters for English text).
    Used to sanity-check chunk sizes without calling the tokenizer.
    """
    return max(1, len(text) // 4)
