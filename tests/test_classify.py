"""Tests for point classification."""

import sys
from pathlib import Path

from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_partition.geometry.classify import (  # noqa: E402
    apply_counts_to_regions,
    classify_points,
    tally_by_region,
)
from market_partition.geometry.orient import Orientation  # noqa: E402
from market_partition.geometry.split import Region  # noqa: E402


def _make_region(rid, label, poly):
    return Region(
        region_id=rid,
        label=label,
        polygon=poly,
        area=poly.area,
        orientation=Orientation(label=label, side_index=1, raw_projection=0.0),
    )


def test_point_in_correct_region():
    north = _make_region(0, "北", Polygon([(0, 5), (10, 5), (10, 10), (0, 10)]))
    south = _make_region(1, "南", Polygon([(0, 0), (10, 0), (10, 5), (0, 5)]))
    pts = [Point(5, 8), Point(5, 2)]
    classified = classify_points(pts, [north, south])
    assert classified[0].region_id == 0
    assert classified[0].region_label == "北"
    assert classified[1].region_id == 1
    assert classified[1].region_label == "南"


def test_point_on_boundary():
    """A point that falls in no region (inside a buffer band) → on_boundary."""
    north = _make_region(0, "北", Polygon([(0, 5.1), (10, 5.1), (10, 10), (0, 10)]))
    south = _make_region(1, "南", Polygon([(0, 0), (10, 0), (10, 4.9), (0, 4.9)]))
    # Point exactly in the 5.1–4.9 gap: not in either.
    classified = classify_points([Point(5, 5)], [north, south])
    assert classified[0].region_id is None
    assert classified[0].region_label == "on_boundary"


def test_tally_and_apply_counts():
    north = _make_region(0, "北", Polygon([(0, 5), (10, 5), (10, 10), (0, 10)]))
    south = _make_region(1, "南", Polygon([(0, 0), (10, 0), (10, 5), (0, 5)]))
    pts = [Point(5, 8), Point(5, 9), Point(5, 2), Point(5, 5)]  # 2 north, 1 south, 1 boundary
    classified = classify_points(pts, [north, south])
    counts = tally_by_region(classified)
    assert counts.get(0) == 2
    assert counts.get(1) == 1
    assert counts.get("on_boundary") == 1
    apply_counts_to_regions([north, south], counts)
    assert north.poi_count == 2
    assert south.poi_count == 1


def test_point_props_carried_through():
    region = _make_region(0, "X", Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]))
    classified = classify_points(
        [Point(5, 5)],
        [region],
        point_props=[{"name": "星巴克", "amenity": "cafe"}],
    )
    assert classified[0].props["name"] == "星巴克"
    assert classified[0].props["amenity"] == "cafe"
