"""FastAPI routes for the market partition tool.

Single substantive endpoint (POST /api/partition) that:
  1. Resolves the region (geocode or explicit polygon).
  2. Fetches each barrier's merged road geometry from OSM.
  3. Runs the split algorithm.
  4. Optionally fetches POIs and classifies them into the pieces.
  5. Returns GeoJSON for the front-end.

Plus health + cache-stats endpoints for ops.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from shapely.geometry import LineString, MultiLineString, Polygon

from .. import __version__
from ..geometry.classify import (
    apply_counts_to_regions,
    classify_points,
    tally_by_region,
)
from ..geometry.split import Barrier, partition
from ..sources.cache import OsmCache
from ..sources.osm import OsmSource
from ..viz.geojson import (
    as_json,
    barrier_to_geojson,
    points_to_geojson,
    regions_to_geojson,
)
from .models import (
    BarrierSpec,
    HealthResponse,
    PartitionRequest,
    PartitionResponse,
    RegionSpec,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# One shared source/cache for the process — reuse across requests.
# If MARKET_PARTITION_PBF is set, barriers/POIs are served from the local PBF
# (offline, fast); otherwise we fall back to the Overpass API.
import os
_DEFAULT_PBF = os.environ.get("MARKET_PARTITION_PBF")
_cache = OsmCache()
_source = OsmSource(cache=_cache, pbf_path=_DEFAULT_PBF)


def _resolve_region(spec: RegionSpec) -> Polygon | MultiPolygon:
    if spec.polygon:
        if len(spec.polygon) < 3:
            raise HTTPException(400, "polygon must have at least 3 [lon,lat] pairs")
        return Polygon(spec.polygon)
    if spec.place:
        try:
            return _source.get_region(spec.place)
        except Exception as e:
            log.exception("region geocode failed")
            raise HTTPException(502, f"failed to geocode region {spec.place!r}: {e}")
    raise HTTPException(400, "region must specify either 'place' or 'polygon'")


def _fetch_barrier(spec: BarrierSpec, region) -> Barrier:
    geom = _source.get_barrier_by_name(
        spec.name,
        region,
        extra_patterns=spec.extra_patterns,
    )
    if geom.is_empty:
        raise HTTPException(
            404,
            f"no OSM ways named {spec.name!r} found in region "
            f"(also tried aliases {spec.extra_patterns})",
        )
    return Barrier(
        name=spec.name,
        kind=spec.kind,
        geometry=geom,
        orient_scheme=spec.orient_scheme,
        extra_patterns=spec.extra_patterns,
    )


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/cache/stats")
def cache_stats() -> dict:
    return _cache.stats()


@router.get("/source")
def source_info() -> dict:
    """Report which data backend is active (PBF local vs Overpass online)."""
    if _source._pbf is not None:
        return {"mode": "pbf", **_source._pbf.stats()}
    return {"mode": "overpass"}


@router.post("/partition", response_model=PartitionResponse)
def partition_endpoint(req: PartitionRequest) -> PartitionResponse:
    # --- 1. region ----------------------------------------------------------
    if not req.region.is_valid():
        raise HTTPException(400, "specify exactly one of region.place / region.polygon")
    if req.cache_bust:
        _source.cache_bust = True
    else:
        _source.cache_bust = False
    region = _resolve_region(req.region)

    # --- 2. barriers --------------------------------------------------------
    barriers = [_fetch_barrier(b, region) for b in req.barriers]

    # --- 3. split -----------------------------------------------------------
    try:
        result = partition(
            region,
            barriers,
            buffer_deg=req.buffer_deg,
            snap_deg=req.snap_deg or 0.003,
        )
    except Exception as e:
        log.exception("partition failed")
        raise HTTPException(500, f"split failed: {e}")

    # --- 4. classify points (optional) -------------------------------------
    points_fc: dict | None = None
    if req.classify_points:
        tags = req.poi_tags or {"amenity": ["restaurant", "cafe", "bar"]}
        try:
            pois = _source.get_pois(region, tags)
        except Exception as e:
            log.warning("POI fetch failed: %s", e)
            pois = None
        if pois is not None and len(pois) > 0:
            pts = list(pois.geometry)
            props = [
                {k: v for k, v in row.items() if k != "geometry"}
                for _, row in pois.iterrows()
            ]
            classified = classify_points(pts, result.pieces, point_props=props)
            counts = tally_by_region(classified)
            apply_counts_to_regions(result.pieces, counts)
            points_fc = points_to_geojson(classified)

    # --- 5. response --------------------------------------------------------
    regions_fc = regions_to_geojson(result.pieces)
    return PartitionResponse(
        features=regions_fc["features"],
        points=points_fc,
        barrier=barrier_to_geojson(result.barrier_used),
        diagnostics={
            **result.diagnostics,
            "n_pieces": len(result.pieces),
            "buffer_deg": result.buffer_deg,
            "labels": [p.label for p in result.pieces],
        },
        cache_stats=_cache.stats(),
    )
