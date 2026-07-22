"""Tests for the splitting algorithm.

These use *synthetic* geometries (no OSM network) so they run fast and
deterministically. The unit of correctness is:
  - the right number of pieces (1 barrier → 2 pieces for a clean cut),
  - the right labels (in/out, north/south),
  - the right areas (cut should preserve total area modulo the buffer band).

The real-data Beijing 5th Ring case is exercised in examples/run_demo.py and
relies on a live OSM connection; here we keep things offline.
"""

import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon

# Ensure the package is importable when running from the project dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_partition.geometry.split import Barrier, partition  # noqa: E402
from market_partition.geometry.orient import label_in_out, label_ns, label_ew  # noqa: E402


def make_square(x0=0, y0=0, size=10):
    return Polygon([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)])


# ============================================================ closed ring ====
def test_closed_ring_splits_into_two_pieces():
    """A closed square-ring barrier should split a region into inside + outside."""
    region = make_square(0, 0, 10)
    # A small square ring at the center, like a mini ring road.
    ring = LineString([(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)])
    barrier = Barrier(name="测试环", kind="closed", geometry=MultiLineString([ring]))

    result = partition(region, [barrier])
    assert len(result.pieces) == 2, f"expected 2 pieces, got {len(result.pieces)}"
    labels = {p.label for p in result.pieces}
    assert "测试环-内" in labels
    assert "测试环-外" in labels


def test_closed_ring_labels_correctly():
    """The piece containing the ring center must be labelled 'inside'."""
    region = make_square(0, 0, 10)
    ring = LineString([(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)])
    barrier = Barrier(name="测试环", kind="closed", geometry=MultiLineString([ring]))

    result = partition(region, [barrier])
    inside = next(p for p in result.pieces if "内" in p.label)
    outside = next(p for p in result.pieces if "外" in p.label)
    # The inside piece must contain the geometric center (5,5).
    assert inside.polygon.contains(Point(5, 5))
    # The outside piece must contain a corner (1,1).
    assert outside.polygon.contains(Point(1, 1))


def test_closed_ring_area_decreases_by_band():
    """Sum of pieces should be ~ region area minus the buffer band."""
    region = make_square(0, 0, 10)
    ring = LineString([(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)])
    barrier = Barrier(name="测试环", kind="closed", geometry=MultiLineString([ring]))

    result = partition(region, [barrier])
    total = sum(p.area for p in result.pieces)
    # Region area is 100. Band removes a thin strip; we expect >90 preserved.
    assert 90 < total < 100, f"total piece area {total} unexpectedly far from 100"


# ============================================================ linear road ====
def test_linear_road_splits_into_two_pieces():
    """A horizontal road across a square region should yield north + south."""
    region = make_square(0, 0, 10)
    road = LineString([(-1, 5), (11, 5)])
    barrier = Barrier(name="测试路", kind="linear", geometry=MultiLineString([road]))

    result = partition(region, [barrier])
    assert len(result.pieces) == 2
    labels = {p.label for p in result.pieces}
    assert "测试路-北" in labels
    assert "测试路-南" in labels


def test_linear_road_ns_labels():
    """North piece should contain a point above the road; south, below."""
    region = make_square(0, 0, 10)
    road = LineString([(-1, 5), (11, 5)])
    barrier = Barrier(name="测试路", kind="linear", geometry=MultiLineString([road]))

    result = partition(region, [barrier])
    north = next(p for p in result.pieces if "北" in p.label)
    south = next(p for p in result.pieces if "南" in p.label)
    assert north.polygon.contains(Point(5, 8))
    assert south.polygon.contains(Point(5, 2))


def test_linear_road_vertical_ew_labels():
    """A vertical road should yield east + west under the 'ew' scheme."""
    region = make_square(0, 0, 10)
    road = LineString([(5, -1), (5, 11)])
    barrier = Barrier(
        name="测试路",
        kind="linear",
        geometry=MultiLineString([road]),
        orient_scheme="ew",
    )
    result = partition(region, [barrier])
    assert len(result.pieces) == 2
    labels = {p.label for p in result.pieces}
    assert "测试路-东" in labels
    assert "测试路-西" in labels


def test_linear_road_diagonal():
    """A diagonal road should still produce 2 pieces (not more)."""
    region = make_square(0, 0, 10)
    road = LineString([(-1, 2), (11, 8)])
    barrier = Barrier(name="斜路", kind="linear", geometry=MultiLineString([road]))
    result = partition(region, [barrier])
    assert len(result.pieces) == 2


# ============================================================ multi-segment ===
def test_gapped_ring_bridges_correctly():
    """A closed ring with a small gap should still bridge and split into 2.

    This mirrors the real OSM situation where ring-road ways are disconnected
    at interchanges. The snap-bridge step should close the gap.
    """
    region = make_square(0, 0, 10)
    # A ring with a 0.2° gap near (3,3) — two separate line segments.
    seg1 = LineString([(3.2, 3), (7, 3), (7, 7), (3, 7)])
    seg2 = LineString([(3, 6.8), (3, 3.2)])
    barrier = Barrier(
        name="断环",
        kind="closed",
        geometry=MultiLineString([seg1, seg2]),
    )
    result = partition(region, [barrier], snap_deg=0.003)
    # With a small gap (< snap_tol default 0.003), the ring should bridge.
    # Allow up to 3 pieces: inside + outside + (maybe a tiny corner sliver).
    assert len(result.pieces) <= 3
    # At least one inside piece must exist and contain the center.
    inside_pieces = [p for p in result.pieces if "内" in p.label]
    assert any(p.polygon.contains(Point(5, 5)) for p in inside_pieces)


# ============================================================ orientation ====
def test_orient_in_out():
    region = make_square(0, 0, 10)
    ring_hull = Polygon([(3, 3), (7, 3), (7, 7), (3, 7)])
    inside_piece = Polygon([(4, 4), (6, 4), (6, 6), (4, 6)])
    outside_piece = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    assert label_in_out(inside_piece, ring_hull).label == "内"
    assert label_in_out(outside_piece, ring_hull).label == "外"


def test_orient_ns():
    road = LineString([(-1, 5), (11, 5)])
    north_piece = Polygon([(0, 6), (10, 6), (10, 10), (0, 10)])
    south_piece = Polygon([(0, 0), (10, 0), (10, 4), (0, 4)])
    assert label_ns(north_piece, road).label == "北"
    assert label_ns(south_piece, road).label == "南"


def test_orient_ew():
    road = LineString([(5, -1), (5, 11)])
    east_piece = Polygon([(6, 0), (10, 0), (10, 10), (6, 10)])
    west_piece = Polygon([(0, 0), (4, 0), (4, 10), (0, 10)])
    assert label_ew(east_piece, road).label == "东"
    assert label_ew(west_piece, road).label == "西"
