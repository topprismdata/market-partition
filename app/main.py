"""FastAPI application entry point.

Mounts the partition API and serves the Leaflet front-end as static files.
Run with:

    uvicorn app.main:app --reload --port 8000

Then open http://localhost:8000/
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Make `market_partition.*` importable when running from app/ directly.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_partition.api.routes import router  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Market Partition",
    description=(
        "Split geographic regions by ring roads, main roads, or rivers using "
        "OpenStreetMap data. Two modes: closed rings (inside/outside) and "
        "linear roads (north/south)."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# Serve the front-end. Mount static dir at /static for assets; the index page
# is served at /.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
def index():
    """Serve the single-page front-end."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))
