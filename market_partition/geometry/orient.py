"""Orientation labels for split regions.

After a region is split into pieces by a barrier, each piece needs a human
label: "inside the ring" / "outside the ring", or "north of the road" /
"south of the road". This module computes those labels from geometry.

Three labelling schemes:
  - "in_out": for a *closed* barrier (ring road). Inside = within the ring's
    convex hull. This handles the common case where the ring data has gaps —
    the convex hull is a robust stand-in for the closed ring polygon.
  - "ns": for a *linear* barrier. Project each piece's centroid onto the
    barrier's normal vector; positive = north, negative = south.
  - "ew": same mechanism, positive = east, negative = west.

The barrier's direction matters for ns/ew: we derive the normal from the
barrier's principal axis (first -> last point of the longest constituent line).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, MultiLineString, Polygon, MultiPolygon


@dataclass(frozen=True)
class Orientation:
    """Result of orienting one split piece."""
    label: str           # "内"/"外", "北"/"南", "东"/"西"
    side_index: int      # +1 / -1, useful for sorting / coloring
    raw_projection: float  # signed projection, for debugging


def _barrier_axis(barrier: LineString | MultiLineString) -> tuple[float, float, float, float]:
    """Return (mid_x, mid_y, normal_x, normal_y) of the barrier's main axis.

    We pick the longest constituent line as the representative direction, take
    its midpoint as the origin, and compute the left-hand normal.
    """
    if barrier.geom_type == "LineString":
        lines = [barrier]
    elif barrier.geom_type == "MultiLineString":
        lines = list(barrier.geoms)
    else:
        lines = []
    if not lines:
        return (0.0, 0.0, 0.0, 1.0)
    longest = max(lines, key=lambda ln: ln.length)
    x0, y0 = longest.coords[0]
    x1, y1 = longest.coords[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy) or 1.0
    # Left-hand normal (rotate +90°): (-dy, dx) / norm.
    nx, ny = -dy / norm, dx / norm
    mid = longest.interpolate(0.5, normalized=True)
    return (mid.x, mid.y, nx, ny)


def label_in_out(piece, ring_hull: Polygon) -> Orientation:
    """Closed-ring scheme: inside vs outside the ring polygon/hull.

    `ring_hull` is the polygon enclosed by the ring (the "hole"). A piece is
    "inside" iff a representative interior point of the piece falls within
    the ring hull. We test the piece's representative_point (a point inside
    the piece guaranteed to not sit in a hole) rather than the centroid,
    because for the outside-of-ring piece the centroid can land inside the
    ring hull (when the ring sits near the region's center).
    """
    rep = piece.representative_point()
    inside = ring_hull.contains(rep) or ring_hull.touches(rep)
    return Orientation(
        label="内" if inside else "外",
        side_index=1 if inside else -1,
        raw_projection=float(inside),
    )


def _label_by_axis(piece, barrier, axis: tuple[float, float], pos_label: str, neg_label: str) -> Orientation:
    """Project centroid onto `axis` (a cardinal direction) relative to barrier midpoint.

    `axis` is the unit vector that defines "positive" (pos_label). For ns we
    use (0, +1) — north is positive; for ew we use (+1, 0) — east is positive.
    This is independent of the barrier's own direction, so a vertical road
    still gives 东/西 correctly.
    """
    mx, my, _, _ = _barrier_axis(barrier)
    c = piece.centroid
    ax, ay = axis
    proj = (c.x - mx) * ax + (c.y - my) * ay
    return Orientation(
        label=pos_label if proj >= 0 else neg_label,
        side_index=1 if proj >= 0 else -1,
        raw_projection=float(proj),
    )


def label_ns(piece, barrier: LineString | MultiLineString) -> Orientation:
    """Linear scheme: north (positive) vs south. Axis = (0,+1)."""
    return _label_by_axis(piece, barrier, (0.0, 1.0), "北", "南")


def label_ew(piece, barrier: LineString | MultiLineString) -> Orientation:
    """Linear scheme: east (positive) vs west. Axis = (+1,0)."""
    return _label_by_axis(piece, barrier, (1.0, 0.0), "东", "西")


SCHEMES = {
    "in_out": label_in_out,
    "ns": label_ns,
    "ew": label_ew,
}


def label_piece(
    piece,
    barrier: LineString | MultiLineString,
    scheme: str,
    ring_hull: Polygon | None = None,
) -> Orientation:
    """Dispatch to the right scheme. ring_hull required for in_out."""
    if scheme == "in_out":
        if ring_hull is None:
            raise ValueError("ring_hull is required for the 'in_out' scheme")
        return label_in_out(piece, ring_hull)
    if scheme in ("ns", "ew"):
        return SCHEMES[scheme](piece, barrier)
    raise ValueError(f"unknown orientation scheme: {scheme!r}")
