"""GeoJSON serialization helpers.

shapely 2.x can emit GeoJSON directly via __geo_interface__, but we want
consistent property payloads for the front-end. These helpers build
FeatureCollections from shapely geometries + our Region/Point dataclasses.
"""

from __future__ import annotations

import json
from typing import Any

from shapely.geometry import mapping, LineString, MultiLineString, Point, Polygon, MultiPolygon


def _feature(geometry: Any, properties: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": mapping(geometry) if geometry is not None else None,
        "properties": properties,
    }


def regions_to_geojson(pieces) -> dict:
    """Convert a list of :class:`Region` to a GeoJSON FeatureCollection."""
    feats = [
        _feature(p.polygon, {
            "region_id": p.region_id,
            "label": p.label,
            "area": round(p.area, 6),
            "poi_count": p.poi_count,
            "side_index": p.orientation.side_index,
        })
        for p in pieces
    ]
    return {"type": "FeatureCollection", "features": feats}


def barrier_to_geojson(barrier: MultiLineString | LineString | None) -> dict | None:
    """Convert the merged barrier line(s) to GeoJSON (for drawing on the map)."""
    if barrier is None or barrier.is_empty:
        return None
    return _feature(barrier, {"kind": "barrier"})


def points_to_geojson(classified) -> dict:
    """Convert classified points to a GeoJSON FeatureCollection.

    Each point carries its region_id/label so the front-end can color it.
    """
    feats = []
    for cp in classified:
        props = {
            "region_id": cp.region_id,
            "region_label": cp.region_label,
        }
        # Merge in original POI tags (name, amenity, ...) if present.
        if cp.props:
            for k, v in cp.props.items():
                # Skip non-serializable / redundant fields.
                if k in ("geometry", "nodes", "ways_bbox"):
                    continue
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else None
                if v is not None and not isinstance(v, (dict,)):
                    props[k] = str(v)
        feats.append(_feature(cp.point, props))
    return {"type": "FeatureCollection", "features": feats}


def as_json(obj: dict) -> str:
    """Serialize with Chinese chars intact (don't escape to \\uXXXX)."""
    return json.dumps(obj, ensure_ascii=False)
