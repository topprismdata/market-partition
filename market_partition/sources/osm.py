"""OSM data layer — fetch regions, barriers (roads), and POIs.

All functions are cached via :mod:`market_partition.sources.cache` to avoid
repeatedly hitting the Overpass/Nominatim APIs. Geometries are returned in
WGS84 (EPSG:4326) degrees, which is what shapely expects for the split math.

Naming note: in Chinese OSM data, a single road like the Beijing 5th Ring Road
is not stored as one relation; it is spread across hundreds of `way` segments
named "北五环"/"东五环"/"南五环"/"西五环"/"五环路". We therefore match by
name regex, not by relation id.
"""

from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path
from typing import Any

import geopandas as gpd
import osmnx as ox
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from .cache import OsmCache, make_key

log = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=FutureWarning)

# osmnx logs verbosely; keep it quiet unless something really breaks.
ox.settings.log_console = False
ox.settings.requests_timeout = 60


def _first(v: Any) -> Any:
    """OSM name/ref tags can be a string or a list (for merged rows); normalize."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _to_multi_line(geom: Any) -> MultiLineString:
    """Coerce any line-ish geometry into a MultiLineString (empty if no lines)."""
    if geom is None:
        return MultiLineString()
    if geom.geom_type == "LineString":
        return MultiLineString([geom])
    if geom.geom_type == "MultiLineString":
        return geom
    if geom.geom_type == "GeometryCollection":
        lines = [g for g in geom.geoms if g.geom_type in ("LineString", "MultiLineString")]
        return unary_union(lines) if lines else MultiLineString()
    return MultiLineString()


def _geojson_to_gdf(geojson_str: str) -> gpd.GeoDataFrame:
    """Parse a GeoJSON FeatureCollection string into a GeoDataFrame.

    We bypass pyogrio (gpd.read_file) because large POI dumps (~MB-scale) with
    many null tag columns cause pyogrio's GeoJSON reader to fail. Building the
    GeoDataFrame from Python dicts is reliable for any size.
    """
    import json
    from shapely.geometry import shape
    data = json.loads(geojson_str)
    feats = data.get("features", [])
    geoms, rows = [], []
    for f in feats:
        g = f.get("geometry")
        if g is None:
            continue
        geoms.append(shape(g))
        # Preserve properties, dropping the synthetic `id`.
        props = dict(f.get("properties") or {})
        rows.append(props)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


class OsmSource:
    """Fetches regions / barriers / POIs from OSM with caching.

    Data source priority when a local PBF is configured (`pbf_path`):
      1. PBF (offline, ~100x faster on repeat queries, no rate limits) — for
         barriers and POIs.
      2. Overpass API (online) — fallback when PBF has no match, and the only
         source for region geocoding (PBFs ship no place-name index).

    Each instance holds its own cache; reuse one instance across requests in
    a long-running server.
    """

    def __init__(
        self,
        cache: OsmCache | None = None,
        cache_bust: bool = False,
        pbf_path: str | Path | None = None,
    ):
        self.cache = cache or OsmCache()
        self.cache_bust = cache_bust
        self._pbf = None
        if pbf_path:
            from .pbf import PbfSource
            self._pbf = PbfSource(pbf_path)
            log.info("OsmSource using local PBF: %s", pbf_path)

    # ------------------------------------------------------------------ region
    def get_region(self, place: str) -> Polygon | MultiPolygon:
        """Resolve a place name to its boundary polygon.

        PBF mode (offline): read from the PBF's admin boundaries.
        Otherwise (online): Nominatim via osmnx, cached in SQLite.
        """
        if self._pbf is not None:
            try:
                return self._pbf.get_region(place)
            except ValueError:
                log.info("PBF has no boundary for %r, falling back to Nominatim", place)
        key = make_key({"op": "region", "place": place})
        cached = None if self.cache_bust else self.cache.get(key)
        if cached:
            gdf = _geojson_to_gdf(cached)
            geom = gdf.iloc[0].geometry
        else:
            gdf = ox.geocoder.geocode_to_gdf(place)
            geom = gdf.iloc[0].geometry
            self.cache.put(key, gdf.to_json())
        return geom

    # ----------------------------------------------------------------- barrier
    def get_barrier_by_name(
        self,
        name: str,
        region: Polygon | MultiPolygon,
        extra_patterns: list[str] | None = None,
    ) -> MultiLineString:
        """Fetch all road segments whose name matches `name` within `region`.

        Returns a merged MultiLineString. `name` is matched as a substring
        (case-insensitive); `extra_patterns` lets callers add aliases like
        "S50" or "G4501" for the 5th Ring Road.
        """
        # Build a regex from the name + aliases. Escape the name so special
        # chars don't break the regex, but allow common OSM variations.
        patterns = [re.escape(name)]
        if extra_patterns:
            patterns += [re.escape(p) for p in extra_patterns]
        regex = re.compile("|".join(patterns), re.IGNORECASE)

        # --- PBF fast path (offline, no rate limit) -------------------------
        if self._pbf is not None:
            geom = self._pbf.get_barrier_by_name(name, region, extra_patterns)
            if not geom.is_empty:
                return geom
            log.info("PBF had no match for %r, falling back to Overpass", name)
        # --- otherwise (or PBF miss) → Overpass online -----------------------

        key = make_key(
            {
                "op": "barrier",
                "name": name,
                "patterns": extra_patterns or [],
                "region_wkt": region.wkt,
            }
        )
        cached = None if self.cache_bust else self.cache.get(key)
        if cached:
            gdf = _geojson_to_gdf(cached)
            lines = [g for g in gdf.geometry if g is not None]
            return _to_multi_line(unary_union(lines)) if lines else MultiLineString()

        # Fetch all highways in the region, then filter by name client-side.
        features = ox.features_from_polygon(region, tags={"highway": True})
        ways = features[features.index.get_level_values("element") == "way"].copy()
        ways = ways[ways["name"].notna()]
        ways["_name"] = ways["name"].apply(_first)
        matched = ways[ways["_name"].apply(lambda v: bool(v and regex.search(str(v))))]
        lines = [g for g in matched.geometry if g is not None]
        merged = unary_union(lines) if lines else MultiLineString()

        # Cache only the matched rows.
        if len(matched) > 0:
            out = gpd.GeoDataFrame(geometry=list(matched.geometry), crs="EPSG:4326")
            self.cache.put(key, out.to_json())
        return _to_multi_line(merged)

    # -------------------------------------------------------------------- POIs
    def get_pois(
        self,
        region: Polygon | MultiPolygon,
        tags: dict[str, Any],
    ) -> gpd.GeoDataFrame:
        """Fetch points-of-interest (e.g. shops, restaurants) in the region.

        `tags` follows osmnx convention, e.g. {"amenity": ["restaurant","cafe"]}.
        Returns a GeoDataFrame of POI points. Empty if none found.
        """
        # --- PBF fast path ---------------------------------------------------
        if self._pbf is not None:
            pois = self._pbf.get_pois(region, tags)
            if len(pois) > 0:
                return pois
            log.info("PBF had no POIs for %r, falling back to Overpass", tags)
        # --- otherwise (or PBF miss) → Overpass online -----------------------

        key = make_key(
            {"op": "pois", "tags": tags, "region_wkt": region.wkt}
        )
        cached = None if self.cache_bust else self.cache.get(key)
        if cached:
            return _geojson_to_gdf(cached)

        try:
            pois = ox.features_from_polygon(region, tags=tags)
        except Exception:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        # POIs come back as polygons (building footprints); reduce to centroids
        # so the split/classify math deals with points.
        pois = pois.copy()
        pois["geometry"] = pois.geometry.centroid
        pois = pois[pois.geometry.notna()]
        # Reset the MultiIndex (osmnx returns (element, osmid)) — to_json writes
        # it as the GeoJSON `id` and pyogrio chokes on tuple ids.
        pois = pois.reset_index(drop=True)
        # Keep only useful columns. OSM returns 200+ sparse tag columns; storing
        # them all bloats the cache to 10MB and crashes pyogrio's GeoJSON reader.
        keep = ["geometry", "name", "name:zh", "amenity", "shop", "cuisine",
                "brand", "phone", "website"]
        keep = [c for c in keep if c in pois.columns]
        pois = pois[keep].copy()
        # Strip list/tuple/set cells (OSM multi-valued tags) — pyogrio can't
        # round-trip them through GeoJSON.
        for col in pois.columns:
            if col == "geometry":
                continue
            pois[col] = pois[col].apply(
                lambda v: v[0] if isinstance(v, (list, tuple)) and v else ("" if isinstance(v, (list, tuple, set)) else v)
            )
        if len(pois) > 0:
            self.cache.put(key, pois.to_json())
        return pois if len(pois) > 0 else gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
