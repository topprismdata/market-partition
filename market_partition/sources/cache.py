"""SQLite-backed cache for OSM query results.

OSM queries (via Overpass/Nominatim) are the slowest and most fragile part of
this tool. A request for "all highways in Beijing" can take 30-60s and may be
rate-limited. Since map data changes slowly, we cache the GeoJSON response
keyed by the query parameters.

Design:
  - Key: a hash of the normalized query dict (stable across runs).
  - Value: GeoJSON string + created timestamp + TTL.
  - TTL defaults to 30 days. Use cache_bust=True to force a refetch.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_DEFAULT_DB_PATH = Path.home() / ".cache" / "market_partition" / "osm_cache.sqlite"


def _normalize(obj: Any) -> Any:
    """Recursively sort dict/list keys so different key orders hash the same."""
    if isinstance(obj, dict):
        return {k: _normalize(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_normalize(x) for x in obj]
    return obj


def make_key(query: dict) -> str:
    """Stable hash of a query dict (order-independent)."""
    payload = json.dumps(_normalize(query), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class OsmCache:
    """Thin SQLite wrapper. Stores GeoJSON text keyed by query hash."""

    def __init__(self, db_path: Path | str | None = None, ttl: int = DEFAULT_TTL_SECONDS):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.ttl = ttl
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        # check_same_thread=False: FastAPI may call from a threadpool worker.
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS osm_cache (
                    key TEXT PRIMARY KEY,
                    geojson TEXT NOT NULL,
                    created REAL NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            c.commit()

    def get(self, key: str) -> str | None:
        """Return cached GeoJSON if present and not expired, else None."""
        with self._conn() as c:
            row = c.execute(
                "SELECT geojson, created FROM osm_cache WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        geojson, created = row
        if time.time() - created > self.ttl:
            return None
        # bump hit count asynchronously (best-effort)
        with self._conn() as c:
            c.execute("UPDATE osm_cache SET hits = hits + 1 WHERE key=?", (key,))
            c.commit()
        return geojson

    def put(self, key: str, geojson: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO osm_cache (key, geojson, created, hits) VALUES (?,?,?,0)",
                (key, geojson, time.time()),
            )
            c.commit()

    def stats(self) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(hits),0) FROM osm_cache"
            ).fetchone()
        return {"entries": row[0], "total_hits": row[1], "db_path": str(self.db_path)}

    def clear(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM osm_cache")
            c.commit()
