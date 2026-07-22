"""Demo: split a region by a barrier and save an interactive folium map.

Two examples, runnable from the CLI:
  1. Beijing 5th Ring Road (closed ring → inside / outside).
  2. A main road in Beijing (linear → north / south).

Usage:
    python examples/run_demo.py beijing_5ring            # case A
    python examples/run_demo.py beijing_changan          # case B (Chang'an Ave)
    python examples/run_demo.py --help

Output: writes examples/output/<name>.html — open in a browser.

Requires network access (Overpass) on first run; results are cached afterwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running directly from the repo: add parent dir to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import folium
import warnings

warnings.filterwarnings("ignore")

from market_partition.geometry.split import Barrier, partition
from market_partition.geometry.classify import (
    apply_counts_to_regions,
    classify_points,
    tally_by_region,
)
from market_partition.sources.osm import OsmSource
from shapely.geometry import Polygon

PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#EECA3B"]


# --------------------------------------------------------------------- cases
def case_beijing_5ring() -> dict:
    """Case A: split the full Beijing municipality by the 5th Ring Road.

    NOTE: region must be the full administrative boundary (not a hand-picked
    bbox). A small bbox around the ring yields an inverted inside/outside
    ratio because the bbox is mostly inside the ring. We geocode "北京市" to
    get the real ~1.7deg² boundary; the ring (~0.07deg² inside) is then ~4%.
    """
    return {
        "name": "beijing_5ring",
        "title": "北京五环路切割 (内/外)",
        "region_place": "北京市",  # geocode at runtime, not a static bbox
        "barrier_name": "五环",
        "extra_patterns": ["S50", "G4501"],
        "kind": "closed",
        "poi_tags": {"amenity": ["restaurant", "cafe"]},
    }


def case_beijing_changan() -> dict:
    """Case B: split a Beijing core bbox by Chang'an Avenue (linear, ns).

    长安街 runs roughly east-west through the heart of Beijing, so a north/south
    split is the natural labelling. The bbox is chosen tight enough that the
    avenue fully spans it east-west (otherwise the buffer band won't cut the
    region into north/south pieces).
    """
    region = Polygon([
        (116.37, 39.89), (116.41, 39.89),
        (116.41, 39.93), (116.37, 39.93),
    ])
    return {
        "name": "beijing_changan",
        "title": "长安街切割 (南/北)",
        "region": region,
        "barrier_name": "长安街",
        "extra_patterns": ["长安街", "长安路"],
        "kind": "linear",
        "poi_tags": {"amenity": ["restaurant", "cafe"]},
    }


CASES = {
    "beijing_5ring": case_beijing_5ring,
    "beijing_changan": case_beijing_changan,
}


def run(case_key: str, out_dir: Path, pbf_path: str | None = None) -> Path:
    spec = CASES[case_key]()
    print(f"\n=== {spec['title']} ===")
    if pbf_path:
        print(f"data source: 本地 PBF ({pbf_path})")
    else:
        print("data source: Overpass 在线 API")

    src = OsmSource(pbf_path=pbf_path)
    # Resolve region: geocode place name if given, else use static polygon.
    if "region_place" in spec:
        print(f"[0/4] 地理编码 region: {spec['region_place']} …")
        region = src.get_region(spec["region_place"])
        spec["region"] = region  # downstream code reads spec["region"]
    else:
        region = spec["region"]
    print(f"   region bounds: {tuple(round(x,3) for x in region.bounds)}, area={region.area:.4f}")

    print("[1/4] 拉取切割要素…")
    geom = src.get_barrier_by_name(
        spec["barrier_name"], region, extra_patterns=spec["extra_patterns"]
    )
    n_seg = len(geom.geoms) if hasattr(geom, "geoms") else 1
    print(f"   {spec['barrier_name']}: {n_seg} 线段")

    print("[2/4] 执行切割…")
    barrier = Barrier(name=spec["barrier_name"], kind=spec["kind"], geometry=geom)
    result = partition(region, [barrier])
    print(f"   切出 {len(result.pieces)} 块 (buffer={result.buffer_deg}°)")
    for p in result.pieces:
        print(f"     {p.label}: 面积 {p.area:.5f} ({p.area/region.area*100:.1f}%)")

    print("[3/4] 拉取并分类 POI…")
    pois = src.get_pois(region, spec["poi_tags"])
    classified = classify_points(list(pois.geometry), result.pieces)
    counts = tally_by_region(classified)
    apply_counts_to_regions(result.pieces, counts)
    for p in result.pieces:
        print(f"     {p.label}: {p.poi_count} 个 POI")
    print(f"     落在缓冲带: {counts.get('on_boundary', 0)}")

    print("[4/4] 生成 folium 地图…")
    out_path = out_dir / f"{spec['name']}.html"
    _render_folium(spec, result, pois, classified, out_path)
    print(f"   → {out_path}")
    return out_path


def _render_folium(spec, result, pois, classified, out_path: Path) -> None:
    cx = spec["region"].centroid.x
    cy = spec["region"].centroid.y
    m = folium.Map(location=[cy, cx], zoom_start=12, tiles="OpenStreetMap")

    # Region polygons (each piece colored by id).
    for p in result.pieces:
        color = PALETTE[p.region_id % len(PALETTE)]
        geo = _polygon_to_leaflet(p.polygon)
        folium.Polygon(
            locations=geo,
            color=color,
            weight=2,
            fillColor=color,
            fillOpacity=0.25,
            popup=folium.Popup(
                f"<b>{p.label}</b><br>面积: {p.area:.5f}<br>POI: {p.poi_count}",
                max_width=200,
            ),
        ).add_to(m)

    # Barrier line in red.
    if result.barrier_used is not None:
        for line in _iter_lines(result.barrier_used):
            folium.PolyLine(
                locations=[(y, x) for x, y in line.coords],
                color="#d62728",
                weight=3,
                dash_array="6 4",
                popup=spec["barrier_name"],
            ).add_to(m)

    # POI points colored by region.
    for cp in classified:
        color = "#888" if cp.region_id is None else PALETTE[cp.region_id % len(PALETTE)]
        folium.CircleMarker(
            location=[cp.point.y, cp.point.x],
            radius=4,
            color=color,
            fillColor=color,
            fillOpacity=0.8,
            popup=cp.region_label,
        ).add_to(m)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))


def _polygon_to_leaflet(poly) -> list:
    """Extract exterior ring coords as [lat, lng] for folium.Polygon."""
    if poly.geom_type == "Polygon":
        rings = [poly.exterior]
    else:  # MultiPolygon — use the largest piece for display.
        biggest = max(poly.geoms, key=lambda p: p.area)
        rings = [biggest.exterior]
    return [(y, x) for x, y in rings[0].coords]


def _iter_lines(geom):
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type == "MultiLineString":
        yield from geom.geoms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "case",
        choices=list(CASES),
        help="which demo case to run",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="output directory for the HTML map",
    )
    parser.add_argument(
        "--pbf",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "beijing-latest.osm.pbf",
        help="local .osm.pbf file (default: data/beijing-latest.osm.pbf if present). "
             "Pass --pbf none to force the Overpass API.",
    )
    args = parser.parse_args()
    pbf = None if str(args.pbf).lower() == "none" else (str(args.pbf) if args.pbf.exists() else None)
    if args.pbf and pbf is None and str(args.pbf).lower() != "none":
        print(f"(PBF not found at {args.pbf}, falling back to Overpass API)")
    out = run(args.case, args.out_dir, pbf_path=pbf)
    print(f"\n✅ 完成。在浏览器打开: file://{out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
