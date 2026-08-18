# Core Geometry — 算法详解

本文档描述市场划分的几何算法细节。README 仅在概念层提到 LLM 解释 / GIS 决定的分工，本文档给出具体公式与实现要点。

---

## 闭合环切割（环路 → 内/外）

OSM 里一条环路是 200-2000 条断开的 way 段（立交桥、匝道导致断裂）。算法流程：

1. **路名归一化匹配**："五环路"自动回退匹配"五环"（OSM 主体路段叫"北五环"不叫"北五环路"）
2. **空间过滤**：剔除远郊同名异义路段（如密云区的"二环路"距真二环 50km）
3. **自适应 buffer**：从 0.0001° 扫到 0.005°，选最窄能切出有实质次大块的宽度
4. **方位判定**：用 `representative_point` + polygonize 多边形判定内/外（polygonize 失败回退 convex_hull）
5. **环重建**：环未闭合时用 [momepy.enclosures](https://docs.momepy.org/)（urban morphology 标准库）重建环内多边形

## 线性切割（主干道 → 南/北）

主干道（如长安街）在 OSM 里只有城市中心的一小段，但人类说"按长安街切北京"是指这条路的**方向概念线**。算法：

1. 取道路主轴方向（最长段的首尾向量）
2. **沿主轴方向向两端射射线，求射线与 region 边界的交点**
3. 把交点拼到原线上，让切割线横贯整个区域
4. 用延长后的线 + buffer 带切割
5. 按法线轴投影判南/北或东/西

## 自适应 buffer 宽度的关键洞察

环路（有立交断口）需要 ~250m 宽的 buffer 桥接断口；线性主干道只需 ~10m。算法从窄到宽扫描，选最窄可用宽度——避免对线性道路"过度糊住"，又能在环路上桥接断口。

## 实现模块

```text
market_partition/geometry/
├── split.py        # 切割 (closed/linear) + 线性延长到边界
├── orient.py       # 方位判定 (内/外, 南/北, 东/西)
└── classify.py     # 点分类 (POI 归到区块)
```

`split.py` 是核心入口，`orient.py` 决定 polygonize / convex_hull 回退顺序，`classify.py` 处理后续 POI 归类。

## 与 OSM 数据质量的关系

- **OSM 路名规范化**：环路的命名在不同城市的差异大（北京"五环"/"五环路"都出现），需要回退匹配
- **OSM 拓扑完整性**：环路的 200-2000 way 段能否正确连接取决于 OSM contributor 的细致程度；某些城市的二环/三环在 OSM 里严重碎片化，必须靠 `momepy.enclosures` 重建
- **OSM 行政边界**：使用 `region` 边界（来自 OSM admin level）作为切割的外部约束；行政边界本身的精度影响最终结果

## 已知局限

- 缓冲带宽度（250m / 10m）是经验值，跨城市迁移时需要重新调参
- `representative_point` 在极端凹形 polygon 上可能落入外部
- 射线延长假设 region 是凸形；对严重凹形的 region（如带状行政区）需要额外处理

## 相关文档

- [`agent-loop.md`](agent-loop.md) — 6 工具详解
- [`validation.md`](validation.md) — 算法在 5 case 上的实测表现
