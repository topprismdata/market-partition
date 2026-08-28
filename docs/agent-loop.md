# Agent Loop — 自验证工具链

本文档详细描述 `market_partition/agent/tools.py` 提供的 6 个自描述工具及其调用约定。README 仅描述概念流程，详细工具说明与完整 demo 见本文档。

---

## 为什么需要 Agent Loop

传统 GIS 工具的痛点：**工具跑完了，但结果对不对要靠人看**。如果切割结果错了（比如环路没闭合、内外颠倒），人没仔细看就会交付错误结果。

Agent Loop 的思路是：**让 LLM 在每次工具调用后看诊断信号，自己判断结果对不对，决定是否需要换策略重试**。这参考了学术界的 autonomous GIS 研究：

- [LLM-Geo](https://giscience.psu.edu/llm-geo-an-open-source-autonomous-gis-prototype/)（宾州州立，2023）提出自主 GIS 的五个目标，其中 **self-verifying（自验证）** 正是这套机制
- [GISclaw](https://arxiv.org/abs/2603.26845)（2026）用 "LLM 推理核 + 持久 Python 沙箱" 实现

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  LLM Agent（判断核）                                     │
│                                                         │
│  每一轮循环:                                             │
│  1. 看上一轮工具返回的 diagnostics + warnings            │
│  2. 判断: 结果合理吗? 哪里不对?                          │
│  3. 决策: 继续下一步 / 换策略重试 / 渲染图做视觉检查     │
└──────────────────┬──────────────────────────────────────┘
                   │ 调用工具
┌──────────────────▼──────────────────────────────────────┐
│  6 个自描述工具 (market_partition/agent/tools.py)       │
│                                                         │
│  每个工具返回 ToolResult:                                │
│    data        — 结构化结果                              │
│    diagnostics — 健康度指标 (LLM 据此判断)               │
│    warnings    — 明确的问题提示                          │
│    render_png  — 可选的渲染图路径 (供视觉检查)           │
└─────────────────────────────────────────────────────────┘
```

## 6 个工具详解

| 工具 | 输入 | 输出 data | 关键 diagnostics | 触发下一步的 warnings |
|------|------|---------|-----------------|---------------------|
| `fetch_barrier` | 路名 + region | 路段 MultiLineString | `n_segments`, `centroid_spread_deg` | spread > 0.1° → 可能有同名异义路段混入 |
| `run_partition` | region + barrier | SplitResult | `inside_area_frac`, `buffer_deg` | inside_frac ≈ 0 → 环没闭合 |
| `reconstruct_ring` | barrier 几何 | 环内多边形 Polygon | `method`, `inside_area`, `top_areas` | 重建失败 → 数据太碎片 |
| `check_landmarks` | result + 期望表 | 对/错地标列表 | `accuracy`, `n_wrong` | accuracy < 1.0 → 需要排查 |
| `render_result` | result + landmarks | PNG 路径 | — | — |
| `visual_check` | PNG 路径 | 视觉判断结果 | `suggested_prompt` | 交给 LLM 的视觉能力判断 |

## 完整 Agent Loop 示例（二环，最难 case）

二环路在 OSM 里碎片严重，直接切割会失败。Agent Loop 的实际运行过程：

```
轮次 1: fetch_barrier("二环")
  → data: 432 段路
  → diagnostics: centroid_spread=0.12°
  → warnings: "spread > 0.1°, 可能有同名异义路段"
  → LLM 判断: 路段数充足,spread 可接受,继续

轮次 2: run_partition(region, barrier, kind="closed")
  → data: SplitResult, 1 块
  → diagnostics: inside_area_frac=0.0
  → warnings: "inside_area_frac 近零, 环可能没闭合"
  → LLM 判断: 环没闭合,需要重建

轮次 3: reconstruct_ring(barrier, region)
  → data: 环内 Polygon, area=0.007
  → diagnostics: method=momepy.enclosures
  → LLM 判断: 重建成功,用新多边形组装结果

轮次 4: check_landmarks(result, expected)
  → diagnostics: accuracy=1.0 (6/6 全对)
  → LLM 判断: 地标全对,做最后视觉确认

轮次 5: render_result(result) → PNG
  → LLM 用视觉能力看图: 红环闭合,蓝色在内,地标全绿
  → LLM 判断: 结果可信,完成
```

**关键**：每一轮 LLM 都在读工具的诊断信号来决策——不是一次性跑完。这正是"自验证"的价值。

## 怎么用

`agent/tools.py` 里的工具**任何 LLM 会话都能直接调**，不需要额外的调度代码或 API key。LLM 本身就是 agent loop 的调度器。

```python
from market_partition.agent import fetch_barrier, run_partition, reconstruct_ring, check_landmarks
from market_partition.sources.osm import OsmSource

src = OsmSource(pbf_path="data/beijing-latest.osm.pbf")
region = src.get_region("北京市")

# 轮次 1
r1 = fetch_barrier(src, "二环", region)
print(r1.warnings)  # 看诊断决定下一步

# 轮次 2
r2 = run_partition(region, r1.data, name="二环路", kind="closed")
if any("near zero" in w for w in r2.warnings):
    # 轮次 3: 环没闭合 → 重建
    r3 = reconstruct_ring(r1.data, region=region)
    # 用 r3.data 组装最终结果

# 轮次 4: 验证
r4 = check_landmarks(result, {"天安门": (116.397, 39.9169, "内"), ...})
print(f"准确率: {r4.diagnostics['accuracy']}")
```

运行完整演示：

```bash
python examples/agent_loop_demo.py    # 二环
python examples/agent_loop_all.py     # 全部 5 case
```

## 边界与失败模式

- LLM 视觉能力依赖具体模型；不同模型对同一渲染图的判断可能不一致
- `centroid_spread` 阈值（0.1°）是经验值，城市尺度差异较大
- `inside_area_frac ≈ 0` 是环没闭合的充分信号，但**不是必要信号**（也可能因为 region 边界本身偏置）
- 重建失败的 case 通常需要更细粒度的 OSM 数据或人工指定候选 way

## 相关文档

- [`geometry.md`](geometry.md) — 几何算法详解
- [`validation.md`](validation.md) — 5 个 case 的完整评估记录
- [`DATA_PROVENANCE.md`](DATA_PROVENANCE.md) — 数据来源与 OSM attribution
