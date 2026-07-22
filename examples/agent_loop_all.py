"""Run the agent loop across all 5 cases, with per-round diagnostics.

For each case, the agent:
  1. fetch_barrier  → reads spatial-spread warning
  2. run_partition  → reads inside_area_frac warning
  3. reconstruct_ring (only if partition warned the ring didn't close)
  4. check_landmarks → accuracy signal
  5. render_result  → PNG for visual check

Every round's diagnostics print, so you can see the agent reacting to signals.
Output: examples/output/agent_<case>.png for each, + a summary table.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import warnings as _w
_w.filterwarnings("ignore")

from market_partition.agent import (
    fetch_barrier, run_partition, reconstruct_ring,
    check_landmarks, render_result,
)
from market_partition.geometry.split import Region, SplitResult
from market_partition.geometry.orient import Orientation
from market_partition.sources.osm import OsmSource
from shapely.geometry import Polygon

PBF = "/Users/ghb/ZCodeProject/market_partition/data/beijing-latest.osm.pbf"
OUT = Path(__file__).resolve().parent / "output"

# Landmarks (lon, lat, expected label substring) — geographic ground truth.
LM_RINGS = {
    "天安门":   (116.397, 39.9169),
    "北京站":   (116.427, 39.902),
    "国贸CBD":  (116.463, 39.909),
    "动物园":   (116.337, 39.942),
    "中关村":   (116.316, 39.984),
    "奥林匹克公园": (116.390, 40.005),
    "望京":     (116.481, 40.000),
    "回龙观":   (116.342, 40.073),
    "首都机场": (116.586, 40.080),
    "亦庄":     (116.541, 39.799),
    "通州":     (116.657, 39.909),
}
# Expected inside/outside per ring.
EXPECT = {
    "二环": {"天安门":"内","北京站":"内","国贸CBD":"外","动物园":"外","中关村":"外","奥林匹克公园":"外","望京":"外","回龙观":"外","首都机场":"外","亦庄":"外","通州":"外"},
    "三环": {"天安门":"内","北京站":"内","国贸CBD":"内","动物园":"内","中关村":"外","奥林匹克公园":"外","望京":"外","回龙观":"外","首都机场":"外","亦庄":"外","通州":"外"},
    "四环": {"天安门":"内","国贸CBD":"内","动物园":"内","中关村":"内","奥林匹克公园":"内","望京":"内","回龙观":"外","首都机场":"外","亦庄":"外","通州":"外"},
    "五环": {"天安门":"内","国贸CBD":"内","动物园":"内","中关村":"内","奥林匹克公园":"内","望京":"内","回龙观":"内","首都机场":"外","亦庄":"外","通州":"外"},
}
# Bounding boxes for zoom rendering per ring (lon_min, lon_max, lat_min, lat_max).
ZOOM = {
    "二环": (116.25, 116.55, 39.80, 40.05),
    "三环": (116.25, 116.60, 39.80, 40.05),
    "四环": (116.20, 116.60, 39.78, 40.08),
    "五环": (116.15, 116.65, 39.70, 40.10),
}

RING_CASES = [
    ("二环", []),
    ("三环", []),
    ("四环", []),
    ("五环", ["S50", "G4501"]),
]


def _build_result_from_recon(bj, barrier_geom, inside_poly, buf):
    inside = Region(0, "内", inside_poly, inside_poly.area,
                    Orientation("内", 1, 1.0))
    outside_poly = bj.difference(inside_poly)
    outside = Region(1, "外", outside_poly, outside_poly.area,
                     Orientation("外", -1, 0.0))
    # Fix labels to include barrier name.
    return SplitResult(region=bj, pieces=[inside, outside],
                       barrier_used=barrier_geom, buffer_deg=buf)


def _landmarks_for(ring_name):
    exp = EXPECT[ring_name]
    return {n: (lo, la, exp[n]) for n, (lo, la) in LM_RINGS.items() if n in exp}


def run_ring_agent(src, bj, ring_name, patterns):
    print(f"\n{'#'*70}\n# CASE: {ring_name}\n{'#'*70}")
    landmarks = _landmarks_for(ring_name)

    # Round 1: fetch
    r1 = fetch_barrier(src, ring_name, bj, extra_patterns=patterns)
    print(f"[R1 fetch] {r1.summary()}")
    for w in r1.warnings: print(f"   ⚠️  {w}")
    if not r1.ok: return None

    # Round 2: plain partition
    r2 = run_partition(bj, r1.data, name=f"{ring_name}路", kind="closed")
    print(f"[R2 partition] {r2.summary()}")
    for w in r2.warnings: print(f"   ⚠️  {w}")

    # Round 3: reconstruct if partition warned
    need_recon = any("did not close" in w or "near zero" in w for w in r2.warnings)
    if need_recon:
        r3 = reconstruct_ring(r1.data, region=bj)
        print(f"[R3 reconstruct] {r3.summary()}")
        if r3.ok:
            result = _build_result_from_recon(bj, r1.data, r3.data, 0.0)
            # relabel with ring name
            for p in result.pieces: p.label = f"{ring_name}路-{p.label}"
        else:
            print("   重建失败,使用原partition结果")
            result = r2.data
    else:
        result = r2.data

    # Round 4: landmark check
    r4 = check_landmarks(result, landmarks)
    print(f"[R4 landmarks] {r4.summary()}")
    for w in r4.warnings: print(f"   ⚠️  {w}")

    # Round 5: render (zoomed)
    out_png = OUT / f"agent_{ring_name}.png"
    r5 = render_result_zoom(result, r1.data, out_png, landmarks, ZOOM[ring_name])
    print(f"[R5 render] {out_png.name}")
    return {
        "case": ring_name,
        "accuracy": r4.diagnostics["accuracy"],
        "n_correct": r4.diagnostics["n_correct"],
        "n_wrong": r4.diagnostics["n_wrong"],
        "used_reconstruct": need_recon,
        "png": out_png,
        "result": result,
        "wrong_landmarks": r4.warnings,
    }


def run_changan_agent(src):
    print(f"\n{'#'*70}\n# CASE: 长安街 (linear)\n{'#'*70}")
    region = Polygon([(116.37, 39.89), (116.41, 39.89), (116.41, 39.93), (116.37, 39.93)])
    r1 = fetch_barrier(src, "长安街", region, extra_patterns=["长安街", "长安路"])
    print(f"[R1 fetch] {r1.summary()}")
    for w in r1.warnings: print(f"   ⚠️  {w}")
    if not r1.ok: return None

    r2 = run_partition(region, r1.data, name="长安街", kind="linear")
    print(f"[R2 partition] {r2.summary()}")
    for w in r2.warnings: print(f"   ⚠️  {w}")

    landmarks = {"天安门": (116.397, 39.9169, "北"), "前门": (116.397, 39.899, "南")}
    r4 = check_landmarks(r2.data, landmarks)
    print(f"[R4 landmarks] {r4.summary()}")
    for w in r4.warnings: print(f"   ⚠️  {w}")

    out_png = OUT / "agent_长安街.png"
    r5 = render_result_zoom(r2.data, r1.data, out_png, landmarks,
                            (116.36, 116.42, 39.88, 39.94))
    print(f"[R5 render] {out_png.name}")
    return {
        "case": "长安街",
        "accuracy": r4.diagnostics["accuracy"],
        "n_correct": r4.diagnostics["n_correct"],
        "n_wrong": r4.diagnostics["n_wrong"],
        "used_reconstruct": False,
        "png": out_png,
        "result": r2.data,
        "wrong_landmarks": r4.warnings,
    }


def render_result_zoom(result, barrier_geom, out_png, landmarks, zoom_bounds):
    """Render with a specified zoom bbox so small rings stay visible."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from market_partition.geometry.split import _flatten_polys

    fig, ax = plt.subplots(figsize=(10, 9))
    for p in result.pieces:
        is_in = "内" in p.label or "北" in p.label
        c = "#4C78A8" if is_in else "#F58518"
        for poly in _flatten_polys(p.polygon):
            if poly.area < 1e-7: continue
            x, y = poly.exterior.xy
            ax.fill(x, y, color=c, alpha=0.5 if is_in else 0.2)
            ax.plot(x, y, color=c, lw=0.6)
    segs = list(barrier_geom.geoms) if barrier_geom.geom_type == "MultiLineString" else [barrier_geom]
    for s in segs:
        x, y = s.xy
        ax.plot(x, y, "r-", lw=1.2, alpha=0.85)
    from shapely.geometry import Point
    for n, (lo, la, e) in landmarks.items():
        act = None
        for p in result.pieces:
            if p.polygon.contains(Point(lo, la)): act = p.label; break
        ok = act and e in act
        ax.plot(lo, la, "o" if ok else "X", ms=10,
                mfc="green" if ok else "red", mec="black", mew=1)
        ax.annotate(f"{n}\n{e}/{act}", (lo, la), xytext=(6, 6),
                    textcoords="offset points", fontsize=8.5, fontweight="bold",
                    color="green" if ok else "red")
    ax.set_xlim(zoom_bounds[0], zoom_bounds[1])
    ax.set_ylim(zoom_bounds[2], zoom_bounds[3])
    ax.set_aspect("equal"); ax.grid(True, alpha=0.3)
    ax.set_title(f"{result.pieces[0].label.split('-')[0]} pieces={len(result.pieces)} buf={result.buffer_deg}")
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close()
    return out_png


def main():
    src = OsmSource(pbf_path=PBF)
    bj = src.get_region("北京市")
    all_results = []
    for ring, pats in RING_CASES:
        r = run_ring_agent(src, bj, ring, pats)
        if r: all_results.append(r)
    r = run_changan_agent(src)
    if r: all_results.append(r)

    print(f"\n{'='*70}\n总结\n{'='*70}")
    print(f"{'case':<8} {'准确率':<8} {'对/错':<8} {'用了重建':<10} {'错误地标'}")
    for r in all_results:
        print(f"{r['case']:<8} {r['accuracy']:<8.2f} {r['n_correct']}/{r['n_correct']+r['n_wrong']:<8} {'是' if r['used_reconstruct'] else '否':<10} {r['wrong_landmarks'] or '无'}")
    print(f"\nPNG图:")
    for r in all_results:
        print(f"  {r['case']}: {r['png']}")


if __name__ == "__main__":
    main()
