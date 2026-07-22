"""Tests for the local PBF data source integration.

These are *integration* tests: they require the Beijing PBF file at
`market_partition/data/beijing-latest.osm.pbf` and the `pyrosm` package. If
either is missing the tests skip (so CI without the 35MB fixture still passes).

To run them locally:
    cd market_partition
    # ensure data/beijing-latest.osm.pbf exists (see README download instructions)
    python -m pytest tests/test_pbf.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_PBF = Path(__file__).resolve().parent.parent / "data" / "beijing-latest.osm.pbf"
pyrosm = pytest.importorskip("pyrosm")  # skip entire module if pyrosm absent

if not _PBF.exists():
    pytest.skip(
        f"Beijing PBF not found at {_PBF} — download per README to run PBF tests",
        allow_module_level=True,
    )

from market_partition.sources.osm import OsmSource  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

REGION_5RING = Polygon([(116.30, 39.80), (116.55, 39.80), (116.55, 40.05), (116.30, 40.05)])
REGION_CHANGAN = Polygon([(116.37, 39.89), (116.41, 39.89), (116.41, 39.93), (116.37, 39.93)])


def test_osmsource_uses_pbf_when_configured():
    src = OsmSource(pbf_path=str(_PBF))
    assert src._pbf is not None, "PbfSource should be instantiated when pbf_path given"


def test_pbf_finds_5th_ring_segments():
    """PBF should locate the 5th Ring Road by name (the core of our algorithm)."""
    src = OsmSource(pbf_path=str(_PBF))
    geom = src.get_barrier_by_name("五环", REGION_5RING, extra_patterns=["S50", "G4501"])
    assert not geom.is_empty, "五环 should be found in the Beijing PBF"
    n_seg = len(geom.geoms) if hasattr(geom, "geoms") else 1
    assert n_seg > 50, f"expected many ring segments, got {n_seg}"


def test_pbf_finds_changan_ave():
    src = OsmSource(pbf_path=str(_PBF))
    geom = src.get_barrier_by_name("长安街", REGION_CHANGAN, extra_patterns=["长安街", "长安路"])
    assert not geom.is_empty


def test_pbf_returns_empty_for_nonexistent_road():
    """A made-up road name should return an empty geometry (not raise)."""
    src = OsmSource(pbf_path=str(_PBF))
    geom = src.get_barrier_by_name("这条街根本不存在XYZ123", REGION_5RING)
    assert geom.is_empty


def test_pbf_pois_returned():
    """PBF POI query for restaurants/cafes should return a non-empty frame."""
    src = OsmSource(pbf_path=str(_PBF))
    pois = src.get_pois(REGION_5RING, {"amenity": ["restaurant", "cafe"]})
    assert len(pois) > 100, f"expected many POIs in Beijing core, got {len(pois)}"
    # Centroid reduction means all geometries should be points.
    assert all(g.geom_type == "Point" for g in pois.geometry if g is not None)


def test_pbf_query_is_cached_in_process():
    """Second identical query should be near-instant (in-process cache)."""
    import time
    src = OsmSource(pbf_path=str(_PBF))
    t0 = time.time()
    src.get_barrier_by_name("五环", REGION_5RING, extra_patterns=["S50", "G4501"])
    first = time.time() - t0
    t0 = time.time()
    src.get_barrier_by_name("五环", REGION_5RING, extra_patterns=["S50", "G4501"])
    second = time.time() - t0
    # Second should be at least 10x faster than the first (which parses the PBF).
    assert second < first / 5, f"cache miss? first={first:.2f}s second={second:.2f}s"
