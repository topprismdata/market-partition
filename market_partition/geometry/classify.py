"""Point classification — assign each POI/point to its split region.

Given a list of split regions (polygons) and a list of points, figure out
which region each point falls into. Points that fall in a barrier buffer band
(between the cut pieces) are flagged as "on_boundary".

Performance: uses shapely.prepared.prep to build a spatial index per region so
each point test is O(1)-ish. For thousands of POIs this is fast; for hundreds
of thousands a real STRtree would help but that's beyond this tool's scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from shapely.geometry import Point
from shapely.prepared import prep

from .split import Region


@dataclass
class ClassifiedPoint:
    """A point annotated with its assigned region (or boundary flag)."""
    point: Point
    region_id: int | None    # None means "on boundary" (inside no piece)
    region_label: str | None
    props: dict | None       # original OSM tags / caller-supplied attributes


def classify_points(
    points: Iterable[Point],
    regions: list[Region],
    point_props: list[dict] | None = None,
) -> list[ClassifiedPoint]:
    """Assign each point to the region containing it.

    `point_props[i]` (optional) is attached to the i-th point as metadata.
    Returns one ClassifiedPoint per input point, in order.
    """
    # Pre-build prepared geometries for fast contains testing.
    prepared = [(r, prep(r.polygon)) for r in regions]
    points = list(points)
    point_props = point_props or [None] * len(points)
    out: list[ClassifiedPoint] = []
    for pt, props in zip(points, point_props):
        assigned: Region | None = None
        for r, pr in prepared:
            if pr.contains(pt):
                assigned = r
                break
        if assigned is not None:
            out.append(
                ClassifiedPoint(
                    point=pt,
                    region_id=assigned.region_id,
                    region_label=assigned.label,
                    props=props,
                )
            )
        else:
            out.append(
                ClassifiedPoint(
                    point=pt,
                    region_id=None,
                    region_label="on_boundary",
                    props=props,
                )
            )
    return out


def tally_by_region(
    classified: list[ClassifiedPoint],
) -> dict[int | str, int]:
    """Count how many points landed in each region.

    Returns {region_id: count} with the special key "on_boundary" for boundary
    points. Used to populate `poi_count` on each Region.
    """
    counts: dict[int | str, int] = {}
    for cp in classified:
        key = cp.region_id if cp.region_id is not None else "on_boundary"
        counts[key] = counts.get(key, 0) + 1
    return counts


def apply_counts_to_regions(
    regions: list[Region],
    counts: dict[int | str, int],
) -> None:
    """Write poi_count back onto each Region (in place)."""
    for r in regions:
        r.poi_count = counts.get(r.region_id, 0)
