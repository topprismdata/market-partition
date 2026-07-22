"""Region splitting — the core algorithm.

Two barrier kinds, one entry point:

  - "closed": a closed ring (ring road, administrative boundary).
    A real ring like the Beijing 5th Ring is stored in OSM as ~400 disconnected
    `way` segments (立交桥 / 匝道 cause gaps). We bridge those gaps by snapping
    endpoints, then build a thin buffer band that acts as a cutting wall, then
    subtract it from the region. Each surviving piece is labelled inside/outside
    by testing its centroid against the ring's convex hull.

  - "linear": an open line (main road, river).
    Same buffer-band subtraction, but pieces are labelled by projecting the
    centroid onto the barrier's normal (north/south, east/west).

Robustness measures (all learned from empirical probing):
  - Snap-bridge gaps: snap(merged, merged, snap_tol) closes endpoint gaps
    under ~snap_tol degrees. Without this, a single ring yields 13+ fragments.
  - Adaptive buffer: start narrow (so we don't merge nearby parallel roads);
    if we still get too many fragments, widen and retry.
  - Tiny-fragment filter: pieces below MIN_AREA_FRAC of the region are noise
    (sliver polygons at ring interchanges) and are dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.geometry.collection import GeometryCollection
from shapely.ops import polygonize, snap, unary_union

from .orient import Orientation, label_piece

log = logging.getLogger(__name__)

# ---- tunables (degrees in WGS84; 0.001° ≈ 85m at Beijing latitude) -----------
DEFAULT_BUFFER_DEG = 0.0003   # ~25m half-width cutting band
DEFAULT_SNAP_DEG = 0.003      # ~250m endpoint bridge for ring gaps
MIN_AREA_FRAC = 0.001         # drop pieces smaller than 0.1% of region area
# A "meaningful" split piece must be at least this fraction of the parent
# region's area. Below this, pieces are gap-slivers/interchange dust, not real
# partitions. 1% is conservative: the 5th Ring's inside is ~4% of Beijing.
MIN_MEANINGFUL_FRAC = 0.01
# Adaptive band widths: start VERY narrow (a clean main road splits at ~10m)
# and widen through to ring-road scale (~250m bridges interchange gaps).
# The split loop picks the *narrowest* width that yields >1 piece, so a clean
# linear road gets 0.0001 and a gappy ring road escalates to 0.003.
BUFFER_FALLBACKS = (0.0001, 0.0003, 0.0008, 0.0015, 0.003, 0.005)

BarrierKind = Literal["closed", "linear"]
OrientScheme = Literal["in_out", "ns", "ew"]


@dataclass
class Barrier:
    """A cutting element.

    `geometry` is the merged road line(s) from OSM. `kind` decides the
    algorithm + default labelling scheme. `orient_scheme` can override the
    default for a linear barrier (e.g. force "ew" on a diagonal road).
    """
    name: str
    kind: BarrierKind
    geometry: MultiLineString
    orient_scheme: OrientScheme | None = None
    extra_patterns: list[str] = field(default_factory=list)

    def default_scheme(self) -> OrientScheme:
        if self.kind == "closed":
            return "in_out"
        return "ns"


@dataclass
class Region:
    """One piece after splitting."""
    region_id: int
    label: str
    polygon: Polygon | MultiPolygon
    area: float
    orientation: Orientation
    poi_count: int = 0


@dataclass
class SplitResult:
    """Full result of a partition operation."""
    region: Polygon | MultiPolygon
    pieces: list[Region]
    barrier_used: MultiLineString | None
    buffer_deg: float
    diagnostics: dict = field(default_factory=dict)

    def to_geojson_props(self) -> list[dict]:
        return [
            {
                "region_id": p.region_id,
                "label": p.label,
                "area": round(p.area, 6),
                "poi_count": p.poi_count,
                "side_index": p.orientation.side_index,
            }
            for p in self.pieces
        ]


# ----------------------------------------------------------------- internals
def _flatten_polys(geom) -> list[Polygon]:
    """Extract a flat list of Polygon from any geometry."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        out = []
        for g in geom.geoms:
            out.extend(_flatten_polys(g))
        return out
    return []


def _bridge_gaps(merged: MultiLineString, snap_tol: float = DEFAULT_SNAP_DEG) -> MultiLineString:
    """Merge constituent line segments into one MultiLineString.

    Note: we deliberately do NOT self-snap here. Empirically, snapping a real
    OSM ring (200+ segments) clones vertices at every near-intersection and
    produces 600+ tiny segments that defeat polygonize. The actual gap-bridging
    for split purposes happens in the buffer step: a wide-enough buffer band
    closes interchange gaps. `snap_tol` is kept in the signature only to honor
    the public API; it's currently unused.
    """
    return _to_multi_line(merged)


def _to_multi_line(geom) -> MultiLineString:
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


def _split_with_buffer(
    region: Polygon | MultiPolygon,
    barrier: MultiLineString,
    buffer_deg: float,
) -> list[Polygon]:
    """Subtract a buffer band of the barrier from the region."""
    belt = barrier.buffer(buffer_deg)
    remainder = region.difference(belt)
    polys = _flatten_polys(remainder)
    return polys


def _reconstruct_ring_polygon(
    barrier: MultiLineString,
    widths: tuple[float, ...] = BUFFER_FALLBACKS,
) -> Polygon | None:
    """Rebuild the enclosed ('inside') polygon of a ring road.

    Real OSM ring data has gaps at interchanges, so polygonize() on the raw
    lines yields nothing. But if we buffer the lines into a band and take the
    band's *boundary*, polygonize on that boundary closes the gaps (the band
    bridges them) and produces the enclosed polygon(s). We pick the buffer
    width that yields the largest enclosed polygon — that's the ring interior.

    This is the right tool for small rings (e.g. 2nd Ring, ~33km) sitting in a
    large region (Beijing municipality, ~1.7deg²): the buffer-subtract method
    fails there because the ring's band is too small to "cut" the huge region,
    but reconstructing the ring polygon directly works regardless of region size.
    """
    best_poly = None
    best_area = 0.0
    for bw in widths:
        belt = barrier.buffer(bw)
        # The band's boundary is a set of closed loops; polygonize recovers
        # the enclosed area(s). The largest one is the ring interior.
        polys = list(polygonize(belt.boundary))
        if not polys:
            continue
        biggest = max(polys, key=lambda p: p.area)
        # Track the biggest interior found across all widths. Don't keep
        # widening once we have a solid polygon — wider buffers distort.
        if biggest.area > best_area:
            best_area = biggest.area
            best_poly = biggest
        if best_area > 0:
            # Good enough — stop before distortion grows.
            break
    return best_poly


def _drop_tiny(polys: list[Polygon], region_area: float, min_frac: float = MIN_AREA_FRAC) -> list[Polygon]:
    """Filter out sliver pieces (noise from interchange gaps)."""
    if region_area <= 0:
        return polys
    threshold = region_area * min_frac
    kept = [p for p in polys if p.area >= threshold]
    log.debug("dropped %d tiny pieces (< %.2e area)", len(polys) - len(kept), threshold)
    return kept


def _has_meaningful_split(polys: list[Polygon], parent_area: float) -> bool:
    """True if the split produced at least one non-dominant real piece.

    A split is "meaningful" when, after dropping tiny slivers, there's a
    secondary piece whose area is at least MIN_MEANINGFUL_FRAC of the parent.
    This rejects the failure mode where a too-narrow buffer only shatters a
    ring interchange into dust while the parent stays 99% intact.
    """
    if len(polys) < 2 or parent_area <= 0:
        return False
    areas = sorted((p.area for p in polys), reverse=True)
    largest = areas[0]
    # The second-largest piece must be a real chunk, not a sliver.
    second = areas[1] if len(areas) > 1 else 0.0
    return second >= parent_area * MIN_MEANINGFUL_FRAC


def _extend_line_to_boundary(
    line: MultiLineString, region: Polygon | MultiPolygon
) -> MultiLineString:
    """Extend a linear barrier so it spans the whole region.

    A main road like Chang'an Ave only exists as ~4km of OSM data in the city
    center, but a human saying "split Beijing by Chang'an Ave" means the road
    as a *direction* — extend it east/west until it hits Beijing's boundary,
    then it cuts the whole region in half.

    We take the road's principal axis (longest constituent segment), shoot a
    ray from each endpoint along that axis, find where each ray hits the region
    boundary, and splice the hits onto the original line. Geometry primitive:
    ray-boundary intersection (LineString ∩ Polygon.boundary).
    """
    segs = list(line.geoms) if line.geom_type == "MultiLineString" else [line]
    segs = [s for s in segs if s.length > 0]
    if not segs:
        return line
    # Principal axis = longest segment. Its direction defines the extension.
    longest = max(segs, key=lambda s: s.length)
    x0, y0 = longest.coords[0]
    x1, y1 = longest.coords[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = dx / norm, dy / norm
    # Bounds-based far distance (well beyond the region's diagonal).
    minx, miny, maxx, maxy = region.bounds
    far = ((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5 * 2 + 1.0

    # Shoot rays from the two extreme endpoints of the whole merged line.
    all_coords = []
    for s in segs:
        all_coords.extend(list(s.coords))
    if not all_coords:
        return line
    xs = [c[0] for c in all_coords]
    ys = [c[1] for c in all_coords]
    # West/SW extreme and east/NE extreme along the axis direction.
    projs = [(c[0] - x0) * ux + (c[1] - y0) * uy for c in all_coords]
    west_idx = projs.index(min(projs))
    east_idx = projs.index(max(projs))
    west_pt = all_coords[west_idx]
    east_pt = all_coords[east_idx]

    bnd = region.boundary
    west_ray = LineString([(west_pt[0] - far * ux, west_pt[1] - far * uy), west_pt])
    east_ray = LineString([east_pt, (east_pt[0] + far * ux, east_pt[1] + far * uy)])
    west_hit = bnd.intersection(west_ray)
    east_hit = bnd.intersection(east_ray)

    new_segs = list(segs)
    if not west_hit.is_empty:
        hp = west_hit.centroid
        new_segs.insert(0, LineString([(hp.x, hp.y), west_pt]))
    if not east_hit.is_empty:
        hp = east_hit.centroid
        new_segs.append(LineString([east_pt, (hp.x, hp.y)]))
    return _to_multi_line(unary_union(new_segs))


# ----------------------------------------------------------------- public API
def partition(
    region: Polygon | MultiPolygon,
    barriers: list[Barrier],
    buffer_deg: float | None = None,
    snap_deg: float = DEFAULT_SNAP_DEG,
) -> SplitResult:
    """Split `region` by each barrier in sequence.

    Multiple barriers are applied in order: the first barrier splits the input
    region into N pieces, the second splits each of those, etc. Each piece
    inherits the *most recent* barrier's orientation label.
    """
    if not barriers:
        raise ValueError("at least one Barrier is required")

    region_area = region.area
    # `region` may be a MultiPolygon (e.g. a city with islands); union to one
    # geometry for clean difference math.
    current = region
    last_barrier_geom: MultiLineString | None = None
    diagnostics: dict = {}
    # Each item: (polygon, label_from_last_barrier)
    frontier: list[tuple[Polygon | MultiPolygon, Region | None]] = [(current, None)]

    for bi, barrier in enumerate(barriers):
        merged = _bridge_gaps(barrier.geometry, snap_tol=snap_deg)
        scheme = barrier.orient_scheme or barrier.default_scheme()
        # Linear barriers (main roads, rivers) must span the whole region to
        # cut it. A road like Chang'an Ave only covers the city center in OSM;
        # extend it along its own axis to the region boundary before splitting.
        if scheme != "in_out":
            merged = _extend_line_to_boundary(merged, region)
        # For closed rings, try to get the *actual* enclosed polygon via
        # polygonize. polygonize only succeeds when the ring is fully closed;
        # for real OSM data with interchange gaps it produces only tiny sliver
        # polygons (the gaps prevent a big enclosed area from forming). In that
        # case we fall back to the convex hull, which is a robust stand-in.
        ring_hull = None
        if scheme == "in_out":
            ring_polys = list(polygonize(merged))
            # Accept polygonize result only if its largest polygon is a real
            # chunk (≥1% of region). Tiny slivers mean polygonize failed.
            if ring_polys:
                biggest = max(ring_polys, key=lambda p: p.area)
                if biggest.area >= region_area * 0.01:
                    ring_hull = biggest
            if ring_hull is None:
                ring_hull = merged.convex_hull

        widths = (buffer_deg,) if buffer_deg else BUFFER_FALLBACKS
        chosen_width = widths[0]
        next_frontier: list[tuple[Polygon | MultiPolygon, Region | None]] = []

        for parent_poly, _parent_region in frontier:
            # Adaptive buffer: real-world ring roads have gaps of 100-250m at
            # interchanges. Two failure modes to avoid:
            #   (a) too narrow → ring doesn't close, region only gets tiny
            #       sliver fragments near the gaps (the main body stays whole).
            #   (b) too wide → buffer eats real blocks, distorts boundaries.
            # Strategy: try narrow first. Accept the width only when the split
            # produces a "meaningful" secondary piece — one whose area is at
            # least MIN_MEANINGFUL_FRAC of the parent (not just gap slivers).
            # This stops the loop from accepting a 0.0001° width that only
            # shatters a ring interchange into dust while leaving 99% intact.
            polys: list[Polygon] = []
            for bw in widths:
                polys = _drop_tiny(
                    _split_with_buffer(parent_poly, merged, bw),
                    region_area,
                )
                chosen_width = bw
                # Meaningful split = at least one non-dominant piece of real size.
                if _has_meaningful_split(polys, parent_poly.area):
                    break
            # If no width produced a meaningful split, `polys` holds the widest.

            for poly in polys:
                orient = label_piece(poly, merged, scheme, ring_hull=ring_hull)
                label = _make_label(barrier, orient)
                region_id = len(next_frontier)
                piece = Region(
                    region_id=region_id,
                    label=label,
                    polygon=poly,
                    area=poly.area,
                    orientation=orient,
                )
                next_frontier.append((poly, piece))

        frontier = next_frontier
        last_barrier_geom = merged
        diagnostics[f"barrier_{bi}_{barrier.name}"] = {
            "pieces_after": len(frontier),
            "buffer_deg": chosen_width,
        }
        if not frontier:
            log.warning("barrier %r produced zero pieces — absorbed entirely?", barrier.name)
            break

    # Final region_id reassignment (sequential after all barriers).
    pieces = [r for _, r in frontier if r is not None]
    for i, p in enumerate(pieces):
        p.region_id = i

    return SplitResult(
        region=region,
        pieces=pieces,
        barrier_used=last_barrier_geom,
        buffer_deg=diagnostics.get(
            f"barrier_0_{barriers[0].name}", {}
        ).get("buffer_deg", buffer_deg or DEFAULT_BUFFER_DEG),
        diagnostics=diagnostics,
    )


def _make_label(barrier: Barrier, orient: Orientation) -> str:
    """Build a human label, e.g. "五环路-内" or "长安街-北".

    Uses the full barrier name verbatim. Suffix-stripping ("五环路"→"五环")
    is intentionally NOT done here: it's ambiguous (cuts off "测试路"→"测试")
    and the full name is clearer on the map. If a short label is wanted it can
    be derived from the front-end.
    """
    return f"{barrier.name}-{orient.label}"
