"""
app/utils/file_parser.py
========================
Extracts plain text from various file formats.
Called by the document upload endpoint before chunking.

Supported formats:
  - .txt  : Direct decode
  - .md   : Direct decode
  - .pdf  : PyMuPDF (fitz) — add to pyproject.toml when needed
  - .docx : python-docx — add to pyproject.toml when needed

To add a new format:
  1. Add the parser function below
  2. Register it in PARSER_MAP
  3. Add the dependency to pyproject.toml
"""

import io
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)


def parse_text(content: bytes, filename: str = "") -> str:
    """
    Extract text from file bytes based on file extension.

    Args:
        content: Raw file bytes
        filename: Original filename (used to detect format)

    Returns:
        Extracted plain text string

    Raises:
        ValueError: If the file format is unsupported
        UnicodeDecodeError: If text file is not valid UTF-8
    """
    ext = Path(filename).suffix.lower() if filename else ".txt"

    if ext in (".txt", ".md", ".csv", ".log", ""):
        return content.decode("utf-8")

    if ext == ".pdf":
        return _parse_pdf(content)

    if ext == ".docx":
        return _parse_docx(content)

    if ext == ".html":
        return _parse_html(content)

    raise ValueError(
        f"Unsupported file format '{ext}'. "
        "Supported: .txt, .md, .pdf, .docx, .html"
    )


def _parse_pdf(content: bytes) -> str:
    """Extract text from PDF using PyMuPDF."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=content, filetype="pdf")
        pages = []
        for page_num, page in enumerate(doc, 1):
            text = page.get_text()
            if text.strip():
                pages.append(f"[Page {page_num}]\n{text}")
        return "\n\n".join(pages)
    except ImportError:
        raise ImportError(
            "PyMuPDF (fitz) is required for PDF parsing. "
            "Install: uv add pymupdf"
        )


def _parse_docx(content: bytes) -> str:
    """Extract text from Word document."""
    try:
        import docx

        doc = docx.Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except ImportError:
        raise ImportError(
            "python-docx is required for DOCX parsing. "
            "Install: uv add python-docx"
        )


def _parse_html(content: bytes) -> str:
    """Extract text from HTML, stripping tags."""
    try:
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "head"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "head"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip and data.strip():
                    self.text_parts.append(data.strip())

        parser = _TextExtractor()
        parser.feed(content.decode("utf-8"))
        return "\n".join(parser.text_parts)
    except Exception as e:
        log.warning("HTML parsing failed, returning raw text", error=str(e))
        return content.decode("utf-8", errors="replace")
