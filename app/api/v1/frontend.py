"""
app/api/v1/frontend.py
=======================
Serves the PWA landing page and static frontend files.
FastAPI mounts the /frontend directory and serves index.html
at the root, so the app is a single deployable unit.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = Path(__file__).parent.parent.parent.parent / "frontend"

router = APIRouter(tags=["Frontend"])


@router.get("/", include_in_schema=False)
async def serve_landing():
    """Serve the PWA landing page."""
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html")
    return HTMLResponse("<h1>Providius</h1><p>Frontend not found.</p>")


@router.get("/manifest.json", include_in_schema=False)
async def serve_manifest():
    return FileResponse(FRONTEND_DIR / "manifest.json", media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
async def serve_sw():
    return FileResponse(
        FRONTEND_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )
