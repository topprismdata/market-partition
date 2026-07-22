"""Agent tool layer — self-describing tools for an LLM-driven partition loop.

Each tool returns a ToolResult with:
  - `data`: the structured payload
  - `diagnostics`: signals an LLM uses to judge "is this result good?"
  - `warnings`: explicit problems found
  - `render_png`: optional path to a rendered visualization for visual checking

Design goal: tools are NOT dumb functions. Each one inspects its own output
and reports health signals, so an LLM agent can decide the next step without
a human in the loop. This mirrors the GISclaw / LLM-Geo "self-verifying"
agent pattern: the tool tells the agent whether the result is trustworthy.

Example agent loop (what an LLM does with these tools):
  1. fetch_barrier("二环") → warnings: ["spatial outliers dropped", "segment centroid spread 0.5°"]
  2. LLM sees the spread → decides to inspect → render_barrier()
  3. visual_check() → "ring not closed, gaps visible"
  4. LLM decides → reconstruct_ring()
  5. partition() → diagnostics: {"inside_area_frac": 0.004}
  6. check_landmarks() → 1 wrong
  7. LLM decides result good enough → done
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from shapely.geometry import MultiLineString, Point, Polygon, MultiPolygon
from shapely.ops import polygonize, unary_union

from ..geometry.split import Barrier, SplitResult, _flatten_polys, partition
from ..sources.osm import OsmSource

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Universal return type. `diagnostics` is what the agent reasons over."""
    ok: bool
    data: Any = None
    diagnostics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    render_png: Path | None = None  # set by tools that produce a visualization

    def summary(self) -> str:
        """One-line human/LLM-readable summary."""
        parts = [f"ok={self.ok}"]
        if self.warnings:
            parts.append(f"warnings={len(self.warnings)}")
        for k, v in self.diagnostics.items():
            if isinstance(v, float):
                parts.append(f"{k}={v:.4g}")
            else:
                parts.append(f"{k}={v}")
        return " | ".join(parts)


# ============================================================ tools =========
def fetch_barrier(
    source: OsmSource,
    name: str,
    region: Polygon | MultiPolygon,
    extra_patterns: list[str] | None = None,
) -> ToolResult:
    """Fetch a road by name. Reports spatial-consistency diagnostics.

    Diagnostics an agent checks:
      - n_segments, centroid_spread_deg: a genuine ring clusters tightly;
        spread > 0.1° suggests same-name roads in different towns slipped in.
      - bounds_vs_region: how the barrier's bbox relates to the region.
    """
    geom = source.get_barrier_by_name(name, region, extra_patterns=extra_patterns)
    if geom.is_empty:
        return ToolResult(ok=False, warnings=[f"no segments matched {name!r}"])

    segs = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
    cxs, cys = [], []
    for s in segs:
        try:
            c = s.centroid
            cxs.append(c.x); cys.append(c.y)
        except Exception:
            pass
    spread = 0.0
    if cxs:
        spread = ((max(cxs) - min(cxs)) ** 2 + (max(cys) - min(cys)) ** 2) ** 0.5
    bounds = geom.bounds

    warnings = []
    if spread > 0.1:
        warnings.append(
            f"segment centroids spread {spread:.3f}° (>0.1°) — possible same-name "
            f"roads in distant towns polluting the match; consider stricter filtering"
        )
    return ToolResult(
        ok=True,
        data=geom,
        diagnostics={
            "n_segments": len(segs),
            "centroid_spread_deg": spread,
            "bounds": tuple(round(b, 4) for b in bounds),
        },
        warnings=warnings,
    )


def run_partition(
    region: Polygon | MultiPolygon,
    barrier_geom: MultiLineString,
    name: str,
    kind: Literal["closed", "linear"],
    orient_scheme: str | None = None,
    buffer_deg: float | None = None,
) -> ToolResult:
    """Run the split. Reports whether the result is structurally sound.

    Diagnostics an agent checks:
      - n_pieces, inside_area_frac: for a closed ring on a large region,
        inside should be a small fraction (e.g. 2nd ring ~0.4% of Beijing).
        inside_area_frac near 0 or near 100 means the split failed.
      - buffer_deg: the width that worked (helps explain failures).
    """
    barrier = Barrier(name=name, kind=kind, geometry=barrier_geom, orient_scheme=orient_scheme)
    result: SplitResult = partition(region, [barrier], buffer_deg=buffer_deg)
    inside_area = sum(p.area for p in result.pieces if "内" in p.label)
    inside_frac = inside_area / region.area if region.area > 0 else 0.0

    warnings = []
    if kind == "closed":
        # Heuristic: a real ring's inside is between 0.05% and 50% of region.
        # Outside that range → ring didn't close (too small) or ate everything.
        if inside_frac < 0.0005:
            warnings.append(
                f"inside_area_frac={inside_frac:.5f} is near zero — ring likely "
                f"did not close; try reconstruct_ring() or widen buffer"
            )
        elif inside_frac > 0.5:
            warnings.append(
                f"inside_area_frac={inside_frac:.4f} > 50% — region may be too "
                f"small relative to ring, or labels inverted"
            )
    return ToolResult(
        ok=True,
        data=result,
        diagnostics={
            "n_pieces": len(result.pieces),
            "inside_area_frac": inside_frac,
            "buffer_deg": result.buffer_deg,
            "labels": [p.label for p in result.pieces],
        },
        warnings=warnings,
    )


def reconstruct_ring(
    barrier_geom: MultiLineString,
    region: Polygon | MultiPolygon | None = None,
) -> ToolResult:
    """Rebuild the enclosed polygon of a ring using momepy.enclosures.

    momepy.enclosures polygonizes a road network into the enclosed faces it
    forms — exactly the "ring interior" we need. This is the standard GIS
    approach (used in urban morphology), not a hand-rolled buffer scan.

    We pick the SECOND-largest enclosure: the largest is the region outside the
    ring, the second-largest is the ring interior. (If the ring has a tiny
    clip region, the largest enclosure IS the exterior.)

    Use when run_partition reports inside_area_frac ≈ 0 (ring didn't close).
    """
    import geopandas as gpd
    try:
        import momepy
    except ImportError:
        return ToolResult(ok=False, warnings=["momepy not installed; cannot reconstruct"])

    barriers_gdf = gpd.GeoDataFrame(
        geometry=[barrier_geom], crs="EPSG:4326"
    )
    # limit: use region if given, else barrier's own bounds + margin.
    if region is None:
        b = barrier_geom.bounds
        margin = 0.05
        region = Polygon([
            (b[0]-margin, b[1]-margin), (b[2]+margin, b[1]-margin),
            (b[2]+margin, b[3]+margin), (b[0]-margin, b[3]+margin),
        ])
    limit_gdf = gpd.GeoDataFrame(geometry=[region], crs="EPSG:4326")

    try:
        enc = momepy.enclosures(primary_barriers=barriers_gdf, limit=limit_gdf)
    except Exception as e:
        return ToolResult(ok=False, warnings=[f"momepy.enclosures failed: {e}"])

    # Sort enclosures by area desc. The biggest = exterior, 2nd biggest = inside.
    enc = enc.sort_values(by=enc.geometry.name, key=lambda s: s.area, ascending=False)
    areas = list(enc.geometry.area)
    if len(areas) < 2:
        return ToolResult(ok=False, warnings=["enclosures produced <2 faces"])
    inside_poly = enc.geometry.iloc[1]  # 2nd largest
    return ToolResult(
        ok=True,
        data=inside_poly,
        diagnostics={
            "method": "momepy.enclosures",
            "n_enclosures": len(enc),
            "inside_area": inside_poly.area,
            "exterior_area": areas[0],
            "top_areas": [round(a, 5) for a in areas[:5]],
        },
    )


def check_landmarks(
    result: SplitResult,
    landmarks: dict[str, tuple[float, float, str]],
) -> ToolResult:
    """Verify known points landed on their expected side.

    `landmarks`: {name: (lon, lat, expected_label_substring)} e.g.
      {"天安门": (116.397, 39.9169, "内")}.
    Returns which landmarks are wrong — the agent's main correctness signal.
    """
    wrong = []
    right = []
    for name, (lon, lat, expected) in landmarks.items():
        pt = Point(lon, lat)
        actual = None
        for p in result.pieces:
            if p.polygon.contains(pt):
                actual = p.label
                break
        ok = actual is not None and expected in actual
        (right if ok else wrong).append({"name": name, "expected": expected, "actual": actual})
    return ToolResult(
        ok=len(wrong) == 0,
        data={"right": right, "wrong": wrong},
        diagnostics={
            "n_correct": len(right),
            "n_wrong": len(wrong),
            "accuracy": len(right) / max(1, len(right) + len(wrong)),
        },
        warnings=[f"{w['name']}: expected {w['expected']}, got {w['actual']}" for w in wrong],
    )


def render_result(
    result: SplitResult,
    barrier_geom: MultiLineString | None,
    out_png: Path,
    landmarks: dict[str, tuple[float, float, str]] | None = None,
) -> ToolResult:
    """Render the split to a PNG for visual checking.

    The agent calls visual_check() on the output path next.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 9))
    for p in result.pieces:
        is_inside = "内" in p.label or "北" in p.label
        c = "#4C78A8" if is_inside else "#F58518"
        polys = _flatten_polys(p.polygon)
        for poly in polys:
            x, y = poly.exterior.xy
            ax.fill(x, y, color=c, alpha=0.5 if is_inside else 0.22)
            ax.plot(x, y, color=c, lw=0.6)
    if barrier_geom is not None:
        segs = list(barrier_geom.geoms) if barrier_geom.geom_type == "MultiLineString" else [barrier_geom]
        for s in segs:
            x, y = s.xy
            ax.plot(x, y, "r-", lw=0.5, alpha=0.75)
    if landmarks:
        for name, (lon, lat, expected) in landmarks.items():
            actual = None
            for p in result.pieces:
                if p.polygon.contains(Point(lon, lat)):
                    actual = p.label; break
            ok = actual is not None and expected in actual
            ax.plot(lon, lat, "o" if ok else "X", ms=9,
                    mfc="green" if ok else "red", mec="black", mew=0.8)
            ax.annotate(f"{name}\n{expected}/{actual}", (lon, lat),
                        xytext=(5, 5), textcoords="offset points", fontsize=7,
                        color="green" if ok else "red")
    ax.set_aspect("equal"); ax.grid(True, alpha=0.3)
    ax.set_title(f"pieces={len(result.pieces)} buffer={result.buffer_deg}")
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=100, bbox_inches="tight")
    plt.close()
    return ToolResult(ok=True, data=out_png, render_png=out_png,
                      diagnostics={"path": str(out_png)})


def visual_check(image_png: Path, prompt: str | None = None) -> ToolResult:
    """Stub for visual self-check.

    In a full agent this calls a vision LLM (GLM-4V / GPT-4V) on the rendered
    PNG to ask "does this split look correct?". Here we only record that a
    visual check is recommended; the actual vision call is done by the host
    agent (e.g. this LLM session) since it already has vision capability.

    The host agent reads `image_png`, looks at it, and returns its judgement
    as the `data` of this result.
    """
    p = Path(image_png)
    return ToolResult(
        ok=p.exists(),
        data=p,
        render_png=p,
        diagnostics={
            "note": "visual check delegated to host agent's vision capability",
            "suggested_prompt": prompt or "Is this region split correct? Are landmarks on the right side? Are there obvious errors?",
        },
    )
