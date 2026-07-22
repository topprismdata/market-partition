"""Local PBF data source — fast, offline, no rate limits.

Geofabrik (https://download.geofabrik.de) publishes per-region `.osm.pbf`
snapshots of OSM data. Reading from a local PBF is ~100x faster than the
Overpass API for repeated queries, works offline, and never gets rate-limited.

This module wraps `pyrosm` and adds a process-level cache: parsing a 35MB PBF
into a road network takes ~10s, so we do it once and keep the GeoDataFrame in
memory for subsequent name searches (which then take ~0.02s).

Why PBF over Shapefile/GeoPackage: only `.osm.pbf` preserves the full original
OSM tags (name, ref, highway). Geofabrik's .shp/.gpkg derivatives normalize
the name field, which breaks our regex matching ("五环" must still hit
"北五环"/"东五环"/etc.). See README.md for the format comparison.
"""

from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path
from typing import Any

import geopandas as gpd
from pyrosm import OSM
from shapely.geometry import MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from .osm import _first, _to_multi_line

log = logging.getLogger(__name__)


def _drop_spatial_outliers(
    gdf: gpd.GeoDataFrame, cluster_tol_deg: float = 0.5
) -> gpd.GeoDataFrame:
    """Drop road segments that are far from the main cluster of matches.

    Same-name roads exist in different towns (e.g. "二环路" in Miyun 50km from
    Beijing's real 2nd Ring). A genuine ring spans a metro area (~0.2-0.4°),
    so we use a generous 0.5° (~50km) tolerance — anything farther than that
    from the median centroid is a different-town same-name road.

    The tight 0.05° tolerance we tried first was wrong: it killed real ring
    segments on the opposite side of the ring (east vs west 3rd ring are ~15km
    apart but are the SAME road). 0.5° keeps a whole metro's ring intact while
    still dropping a 50km-distant same-name road in another district.
    """
    if len(gdf) <= 2:
        return gdf  # nothing to cluster
    cents = gdf.geometry.centroid
    cxs = [c.x for c in cents]
    cys = [c.y for c in cents]
    import statistics
    cx = statistics.median(cxs)
    cy = statistics.median(cys)
    dists = [((p.x - cx) ** 2 + (p.y - cy) ** 2) ** 0.5 for p in cents]
    keep_mask = [d <= cluster_tol_deg for d in dists]
    n_drop = sum(1 for m in keep_mask if not m)
    if n_drop > 0:
        log.info("dropped %d spatially outlying segments (>%d° from cluster)",
                 n_drop, int(cluster_tol_deg * 1000))
    return gdf[keep_mask].copy()


class PbfSource:
    """Read regions / barriers / POIs from a local `.osm.pbf` file.

    The PBF is parsed lazily on first access to each layer (roads / POIs /
    boundaries) and cached on the instance for the process lifetime. For a
    long-running server, create one PbfSource and reuse it.

    Boundary (region) lookup: PBFs don't ship a geocoded place-name index, so
    we can't resolve "北京市" -> polygon from PBF alone. Region resolution
    still goes through Nominatim (in OsmSource); PBF covers barriers + POIs.
    """

    def __init__(self, pbf_path: str | Path):
        self.pbf_path = Path(pbf_path)
        if not self.pbf_path.exists():
            raise FileNotFoundError(f"PBF not found: {self.pbf_path}")
        self._osm: OSM | None = None
        self._roads: gpd.GeoDataFrame | None = None
        self._pois: gpd.GeoDataFrame | None = None
        self._boundaries: gpd.GeoDataFrame | None = None

    # ------------------------------------------------------------ lazy loaders
    def _reader(self) -> OSM:
        if self._osm is None:
            log.info("opening PBF %s", self.pbf_path)
            self._osm = OSM(str(self.pbf_path))
        return self._osm

    def _get_roads(self) -> gpd.GeoDataFrame:
        """All drivable ways, parsed once and cached."""
        if self._roads is None:
            self._roads = self._reader().get_network(network_type="driving")
            log.info("parsed %d road ways from PBF", len(self._roads))
        return self._roads

    def _get_boundaries(self) -> gpd.GeoDataFrame:
        """Administrative boundaries, parsed once and cached."""
        if self._boundaries is None:
            self._boundaries = self._reader().get_boundaries()
            log.info("parsed %d boundaries from PBF", len(self._boundaries))
        return self._boundaries

    def _get_pois(self) -> gpd.GeoDataFrame:
        """All POIs, parsed once and cached."""
        if self._pois is None:
            try:
                self._pois = self._reader().get_pois()
                log.info("parsed %d POIs from PBF", len(self._pois))
            except Exception as e:
                log.warning("PBF POI parse failed: %s", e)
                self._pois = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        return self._pois

    # --------------------------------------------------------------- barriers
    def get_region(self, place: str) -> Polygon | MultiPolygon:
        """Resolve a place name to its boundary from the PBF's admin boundaries.

        Matches by substring on the `name` column (e.g. "北京市" or "北京").
        Returns the largest matching boundary's geometry. This lets region
        resolution work fully offline — no Nominatim call needed.
        """
        bnd = self._get_boundaries()
        mask = bnd["name"].astype(str).str.contains(place, na=False)
        hits = bnd[mask]
        if len(hits) == 0:
            # Try without the 市/区 suffix.
            short = place.rstrip("市区县")
            if short and short != place:
                hits = bnd[bnd["name"].astype(str).str.contains(short, na=False)]
        if len(hits) == 0:
            raise ValueError(f"no boundary named {place!r} in PBF")
        # Pick the largest by area (the province-level boundary, not a district).
        hits = hits.copy()
        hits["_area"] = hits.geometry.area
        return hits.sort_values("_area", ascending=False).iloc[0].geometry

    def get_barrier_by_name(
        self,
        name: str,
        region: Polygon | MultiPolygon,
        extra_patterns: list[str] | None = None,
    ) -> MultiLineString:
        """Return merged road geometry matching `name` within `region`.

        Two filters are applied:
          1. Name regex match (substring, case-insensitive).
          2. Spatial cluster filter: a real ring road is a *contiguous* feature
             whose segments cluster together. Same-name roads in far-flung
             towns (e.g. "二环路" in Miyun vs the real Beijing 2nd Ring)
             pollute the geometry and must be dropped. We keep only segments
             within `cluster_tol` of the densest cluster of matches.

        Name normalization: OSM stores the 5th Ring Road as "北五环"/"东五环"
        (no "路" suffix), but users naturally type "五环路". If the full name
        matches few segments, we retry with common suffixes stripped ("路",
        "大道", "大街") so "五环路" falls back to "五环" and catches all segments.
        """
        # Build candidate name variants to try in order.
        name_variants = [name]
        for suffix in ("路", "大道", "大街"):
            if name.endswith(suffix) and len(name) > len(suffix):
                name_variants.append(name[: -len(suffix)])

        roads = self._get_roads()
        named = roads[roads["name"].notna()] if "name" in roads.columns else roads.iloc[:0]

        # Try each variant; use the first that yields a substantial match.
        # A "substantial" match means more than 5x the smallest variant, to
        # avoid picking a too-broad variant that matches unrelated roads.
        best_matched = None
        best_count = 0
        for variant in name_variants:
            patterns = [re.escape(variant)] + [re.escape(p) for p in (extra_patterns or [])]
            regex = re.compile("|".join(patterns), re.IGNORECASE)
            matched = named[named["name"].apply(lambda v: bool(regex.search(str(_first(v)))))]
            if len(matched) == 0:
                continue
            matched = matched[matched.geometry.intersects(region)]
            if len(matched) == 0:
                continue
            matched = _drop_spatial_outliers(matched)
            if len(matched) > best_count:
                best_count = len(matched)
                best_matched = matched
            # If this variant already gives a rich match (≥50 segments), use it.
            if len(matched) >= 50:
                break

        if best_matched is None or len(best_matched) == 0:
            return MultiLineString()
        lines = [g for g in best_matched.geometry if g is not None]
        return _to_multi_line(unary_union(lines)) if lines else MultiLineString()

    # ------------------------------------------------------------------ POIs
    def get_pois(
        self,
        region: Polygon | MultiPolygon,
        tags: dict[str, Any],
    ) -> gpd.GeoDataFrame:
        """POIs in `region` whose tags match `tags` (osmnx-style).

        `tags` like {"amenity": ["restaurant","cafe"]} -> keep rows where the
        `amenity` column is one of the listed values.
        """
        pois = self._get_pois()
        if len(pois) == 0:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

        # Filter by tag. pyrosm flattens tags into columns (amenity, shop, ...).
        def tag_matches(row) -> bool:
            for key, vals in tags.items():
                if key not in row.index:
                    continue
                cell = row[key]
                if cell is None or (isinstance(cell, float) and cell != cell):
                    continue
                cell_str = str(_first(cell))
                want = vals if isinstance(vals, (list, tuple)) else [vals]
                if any(str(w).lower() in cell_str.lower() for w in want):
                    return True
            return False

        mask = pois.apply(tag_matches, axis=1)
        pois = pois[mask].copy()
        if len(pois) == 0:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

        # Clip to region; reduce polygons to centroids (consistent with OsmSource).
        pois = pois[pois.geometry.intersects(region)].copy()
        with warnings.catch_warnings():
            # centroid on geographic CRS warns about precision loss; we only use
            # the point for contains-testing, not distance measurement.
            warnings.simplefilter("ignore", UserWarning)
            pois["geometry"] = pois.geometry.centroid
        pois = pois[pois.geometry.notna()].reset_index(drop=True)
        # Keep only useful columns (avoid sparse-tag bloat downstream).
        keep = ["geometry", "name", "amenity", "shop", "cuisine", "brand"]
        keep = [c for c in keep if c in pois.columns]
        return pois[keep] if len(pois) > 0 else gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    # ------------------------------------------------------------- diagnostics
    def stats(self) -> dict:
        return {
            "pbf_path": str(self.pbf_path),
            "roads_loaded": self._roads is not None,
            "pois_loaded": self._pois is not None,
            "boundaries_loaded": self._boundaries is not None,
            "n_roads": len(self._roads) if self._roads is not None else 0,
            "n_pois": len(self._pois) if self._pois is not None else 0,
            "n_boundaries": len(self._boundaries) if self._boundaries is not None else 0,
        }
