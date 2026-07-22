"""Agent-loop demonstration on the 2nd Ring Road.

This script doesn't contain a hardcoded "correct" pipeline. It calls the
self-describing agent tools in sequence, printing each tool's diagnostics
and warnings, so the decision logic is visible. A real LLM agent would read
the same diagnostics and make the same decisions automatically.

The point: the TOOLS tell the agent what's wrong. The agent doesn't need to
guess — it reads `warnings` and `diagnostics` and reacts.

Run:  python examples/agent_loop_demo.py
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
from market_partition.sources.osm import OsmSource

PBF = "/Users/ghb/ZCodeProject/market_partition/data/beijing-latest.osm.pbf"
OUT = Path(__file__).resolve().parent / "output"

# Ground-truth landmarks for verification (2nd Ring).
LANDMARKS_2RING = {
    "天安门":   (116.397, 39.9169, "内"),
    "北京站":   (116.427, 39.902,  "内"),
    "国贸CBD":  (116.463, 39.909,  "外"),
    "中关村":   (116.316, 39.984,  "外"),
    "亦庄":     (116.541, 39.799,  "外"),
    "首都机场": (116.586, 40.080,  "外"),
}


def banner(step, text):
    print(f"\n{'='*60}\n[Agent 轮次 {step}] {text}\n{'='*60}")


def main():
    src = OsmSource(pbf_path=PBF)

    # ----- Round 1: fetch the barrier -----
    banner(1, "fetch_barrier('二环') — 取切割要素")
    bj = src.get_region("北京市")
    r1 = fetch_barrier(src, "二环", bj)
    print(f"  结果: {r1.summary()}")
    for w in r1.warnings:
        print(f"  ⚠️  {w}")
    barrier_geom = r1.data

    # ----- Round 2: try a plain partition -----
    banner(2, "run_partition() — 直接切割,看结果是否可信")
    r2 = run_partition(bj, barrier_geom, name="二环路", kind="closed")
    print(f"  结果: {r2.summary()}")
    for w in r2.warnings:
        print(f"  ⚠️  {w}")
    print("  → 诊断信号: inside_area_frac 接近 0 说明环没闭合")

    # ----- Round 3: agent reacts to warning — reconstruct ring -----
    banner(3, "reconstruct_ring() — 环没闭合,重建环内多边形")
    r3 = reconstruct_ring(barrier_geom)
    print(f"  结果: {r3.summary()}")
    for w in r3.warnings:
        print(f"  ⚠️  {w}")

    # ----- Round 4: build a SplitResult from the reconstructed polygon -----
    banner(4, "用重建的多边形组装切割结果 + 地标核验")
    from market_partition.geometry.split import Region, SplitResult
    from market_partition.geometry.orient import Orientation
    if r3.data is not None:
        inside = Region(
            region_id=0, label="二环路-内", polygon=r3.data, area=r3.data.area,
            orientation=Orientation(label="内", side_index=1, raw_projection=1.0),
        )
        outside_poly = bj.difference(r3.data)
        outside = Region(
            region_id=1, label="二环路-外", polygon=outside_poly, area=outside_poly.area,
            orientation=Orientation(label="外", side_index=-1, raw_projection=0.0),
        )
        result = SplitResult(region=bj, pieces=[inside, outside],
                             barrier_used=barrier_geom, buffer_deg=r3.diagnostics["buffer_deg"])
        print(f"  内{inside.area/bj.area*100:.3f}% 外{outside.area/bj.area*100:.3f}%")
    else:
        print("  重建失败,无法继续")
        return

    r4 = check_landmarks(result, LANDMARKS_2RING)
    print(f"  地标核验: {r4.summary()}")
    for w in r4.warnings:
        print(f"  ⚠️  {w}")

    # ----- Round 5: render + (agent does visual check) -----
    banner(5, "render_result() — 渲染图,交给视觉判断")
    out_png = OUT / "agent_2ring.png"
    r5 = render_result(result, barrier_geom, out_png, landmarks=LANDMARKS_2RING)
    print(f"  渲染: {r5.summary()}")
    print(f"\n  图已保存: {out_png}")
    print("  → 下一步: 主 agent (带视觉) 看图,判断切割是否合理,")
    print("     如有不对应地标,决定是否回到轮次3换 buffer 宽度重试。")


if __name__ == "__main__":
    main()
