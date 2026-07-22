"""Pydantic schemas for the partition API.

These define the request/response contract used by the FastAPI routes and the
Leaflet front-end. Everything is JSON-serializable; shapely geometries are
converted to GeoJSON by the route layer, not here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RegionSpec(BaseModel):
    """How to identify the region to split.

    Exactly one of `place` or `polygon` must be provided:
      - `place`: a geocodable name ("北京市", "海淀区").
      - `polygon`: an explicit boundary as [lon, lat] pairs.
    """
    place: str | None = None
    polygon: list[list[float]] | None = None  # [[lon,lat], ...]

    def is_valid(self) -> bool:
        return (self.place is None) != (self.polygon is None)  # xor


class BarrierSpec(BaseModel):
    """A cutting element specification.

    `kind`:
      - "closed": a closed ring (ring road, admin boundary) → inside/outside.
      - "linear": an open line (main road, river) → north/south or east/west.

    `name`: the road name to match in OSM (e.g. "五环路"). Matching is
    substring-based, so "五环" also matches "北五环"/"东五环" etc.

    `extra_patterns`: extra name aliases for robustness, e.g. ["S50","G4501"].

    `orient_scheme`: override the default labelling. For a linear barrier on
    a diagonal road you may want "ew" instead of the default "ns".
    """
    name: str
    kind: Literal["closed", "linear"]
    extra_patterns: list[str] = Field(default_factory=list)
    orient_scheme: Literal["in_out", "ns", "ew"] | None = None


class PartitionRequest(BaseModel):
    """Top-level request to POST /api/partition."""
    region: RegionSpec
    barriers: list[BarrierSpec]
    classify_points: bool = False
    poi_tags: dict[str, Any] | None = None  # e.g. {"amenity": ["restaurant","cafe"]}
    buffer_deg: float | None = None         # advanced: override band width
    snap_deg: float | None = None           # advanced: override bridge tolerance
    cache_bust: bool = False                # force refetch from OSM


class PieceProperties(BaseModel):
    region_id: int
    label: str
    area: float
    poi_count: int = 0
    side_index: int


class PartitionResponse(BaseModel):
    """GeoJSON FeatureCollection wrapper + diagnostics.

    `features` are the split region polygons. `points` (optional) are the
    classified POIs as a separate FeatureCollection. `diagnostics` carries
    buffer widths / fragment counts for transparency.
    """
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[dict]                  # GeoJSON Features (free-form props)
    points: dict | None = None            # GeoJSON FeatureCollection of POIs
    barrier: dict | None = None           # GeoJSON of the barrier line(s)
    diagnostics: dict = Field(default_factory=dict)
    cache_stats: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
