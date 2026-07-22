"""Render every split case to PNG for visual verification.

For each case we draw:
  - the split pieces (blue=inside/north, orange=outside/south)
  - the barrier line in red
  - known landmarks as triangles, each annotated with its EXPECTED side
    so a human (or vision model) can instantly see if it landed wrong.

Outputs PNGs to examples/output/verify_*.png.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import warnings
warnings.filterwarnings("ignore")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.geometry import Point

from market_partition.sources.osm import OsmSource
from market_partition.geometry.split import Barrier, partition

PBF = "/Users/ghb/ZCodeProject/market_partition/data/beijing-latest.osm.pbf"
OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)

# Landmarks with EXPECTED side relative to each ring (for visual verification).
# Format: name -> (lon, lat, {ring: expected_label})
LM = {
    "天安门":     (116.397, 39.9169),
    "故宫":       (116.397, 39.916),
    "国贸CBD":    (116.463, 39.909),
    "北京站":     (116.427, 39.902),
    "动物园":     (116.337, 39.942),
    "中关村":     (116.316, 39.984),
    "奥林匹克公园": (116.390, 40.005),
    "望京":       (116.481, 40.000),
    "首都机场":   (116.586, 40.080),
    "亦庄":       (116.541, 39.799),
    "通州":       (116.657, 39.909),
    "回龙观":     (116.342, 40.073),
}
# Which side each landmark should be on, per ring (geographic ground truth).
EXPECTED = {
    "二环": {"天安门":"内","北京站":"内","国贸CBD":"外","动物园":"外","中关村":"外","首都机场":"外","亦庄":"外","通州":"外","奥林匹克公园":"外","望京":"外","回龙观":"外"},
    "三环": {"天安门":"内","北京站":"内","国贸CBD":"内","动物园":"内","中关村":"外","首都机场":"外","亦庄":"外","通州":"外","奥林匹克公园":"外","望京":"外","回龙观":"外"},
    "四环": {"天安门":"内","国贸CBD":"内","动物园":"内","中关村":"内","奥林匹克公园":"内","望京":"内","首都机场":"外","亦庄":"外","通州":"外","回龙观":"外"},
    "五环": {"天安门":"内","国贸CBD":"内","动物园":"内","中关村":"内","奥林匹克公园":"内","望京":"内","回龙观":"内","首都机场":"外","亦庄":"外","通州":"外"},
}


def run_ring(ring_name, name_patterns):
    src = OsmSource(pbf_path=PBF)
    bj = src.get_region("北京市")
    geom = src.get_barrier_by_name(ring_name, bj, extra_patterns=name_patterns)
    barrier = Barrier(name=f"{ring_name}路", kind="closed", geometry=geom)
    result = partition(bj, [barrier])
    return src, bj, geom, result


def render_ring(ring_name, bj, geom, result, out_path):
    fig, ax = plt.subplots(figsize=(10, 9))
    inside_area = sum(p.area for p in result.pieces if "内" in p.label)
    total = bj.area

    for p in result.pieces:
        is_inside = "内" in p.label
        c = "#4C78A8" if is_inside else "#F58518"
        polys = [p.polygon] if p.polygon.geom_type == "Polygon" else list(p.polygon.geoms)
        for poly in polys:
            x, y = poly.exterior.xy
            ax.fill(x, y, color=c, alpha=0.55 if is_inside else 0.22)
            ax.plot(x, y, color=c, lw=0.6)

    for g in geom.geoms:
        x, y = g.xy
        ax.plot(x, y, "r-", lw=0.5, alpha=0.75)

    exp = EXPECTED.get(ring_name, {})
    for name, (lon, lat) in LM.items():
        if not (115.3 < lon < 117.5 and 39.1 < lat < 41.1):
            continue
        # find actual label
        actual = None
        for p in result.pieces:
            if p.polygon.contains(Point(lon, lat)):
                actual = "内" if "内" in p.label else "外"
                break
        expect = exp.get(name)
        ok = (expect is None) or (actual == expect)
        marker = "o" if ok else "X"
        mcolor = "green" if ok else "red"
        ax.plot(lon, lat, marker=marker, ms=9, mfc=mcolor, mec="black", mew=0.8)
        tag = f"{name}"
        if expect:
            tag += f"\n期望{expect}/{actual}"
        ax.annotate(tag, (lon, lat), xytext=(5, 5), textcoords="offset points",
                    fontsize=7.5, fontweight="bold",
                    color="green" if ok else "red")

    ax.set_title(f"{ring_name}路切割 — 内{inside_area/total*100:.1f}% / 外{(total-inside_area)/total*100:.1f}% "
                 f"(buffer={result.buffer_deg})\n地标: ✓绿=正确 ✗红=错误", fontsize=11)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)
    ax.set_xlim(115.5, 117.6)
    ax.set_ylim(39.2, 41.0)
    plt.tight_layout()
    plt.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close()


def run_changan():
    """Linear case: Chang'an Ave splits a tight bbox north/south."""
    src = OsmSource(pbf_path=PBF)
    region = __import__("shapely").geometry.Polygon([
        (116.37, 39.89), (116.41, 39.89), (116.41, 39.93), (116.37, 39.93),
    ])
    geom = src.get_barrier_by_name("长安街", region, extra_patterns=["长安街", "长安路"])
    barrier = Barrier(name="长安街", kind="linear", geometry=geom)
    result = partition(region, [barrier])
    return src, region, geom, result


def render_changan(region, geom, result, out_path):
    fig, ax = plt.subplots(figsize=(9, 7))
    for p in result.pieces:
        c = "#4C78A8" if "北" in p.label else "#F58518"
        polys = [p.polygon] if p.polygon.geom_type == "Polygon" else list(p.polygon.geoms)
        for poly in polys:
            x, y = poly.exterior.xy
            ax.fill(x, y, color=c, alpha=0.45)
            ax.plot(x, y, color=c, lw=1.0)
    for g in geom.geoms:
        x, y = g.xy
        ax.plot(x, y, "r-", lw=1.0)
    # Two test points: north of Chang'an, south of Chang'an
    tests = {"天安门(北)": (116.397, 39.9169, "北"), "前门(南)": (116.397, 39.899, "南")}
    for name, (lon, lat, expect) in tests.items():
        actual = None
        for p in result.pieces:
            if p.polygon.contains(Point(lon, lat)):
                actual = "北" if "北" in p.label else "南"
                break
        ok = actual == expect
        ax.plot(lon, lat, "o" if ok else "X", ms=10,
                mfc="green" if ok else "red", mec="black", mew=0.8)
        ax.annotate(f"{name}\n期望{expect}/{actual}", (lon, lat),
                    xytext=(5, 5), textcoords="offset points", fontsize=8,
                    fontweight="bold", color="green" if ok else "red")
    ax.set_title(f"长安街切割(线性) — buffer={result.buffer_deg}")
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close()


def main():
    cases = [
        ("二环", []),              # 中文名匹配已足够; S2 会误匹配京藏高速联络线
        ("三环", []),              # S3 同理不可靠
        ("四环", []),              # S4 同理不可靠
        ("五环", ["S50", "G4501"]),  # 五环 ref 可靠(无歧义)
    ]
    for ring, pats in cases:
        print(f"\n=== {ring} ===")
        src, bj, geom, result = run_ring(ring, pats)
        inside = sum(p.area for p in result.pieces if "内" in p.label)
        print(f"  pieces={len(result.pieces)} buffer={result.buffer_deg} "
              f"内{inside/bj.area*100:.2f}% 外{(bj.area-inside)/bj.area*100:.2f}%")
        # programmatic landmark check
        exp = EXPECTED[ring]
        wrong = []
        for name, expect in exp.items():
            lon, lat = LM[name]
            actual = None
            for p in result.pieces:
                if p.polygon.contains(Point(lon, lat)):
                    actual = "内" if "内" in p.label else "外"; break
            status = "✓" if actual == expect else "✗"
            if actual != expect:
                wrong.append(f"{name}(期望{expect}实{actual})")
            print(f"    {status} {name}: 期望{expect} 实际{actual}")
        out = OUT / f"verify_{ring}.png"
        render_ring(ring, bj, geom, result, out)
        print(f"  → {out}  地标错误: {wrong or '无'}")

    print("\n=== 长安街 ===")
    src, region, geom, result = run_changan()
    for p in result.pieces:
        print(f"  {p.label}: {p.area/region.area*100:.1f}%")
    out = OUT / "verify_changan.png"
    render_changan(region, geom, result, out)
    print(f"  → {out}")


if __name__ == "__main__":
    main()
