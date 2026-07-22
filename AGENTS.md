# AGENTS.md — Market Partition Agent 操作指南

> 本文件给 AI coding agent（Claude Code / Cursor / 等）看。任何 agent 接手本项目前，先读这个文件。

## 项目是什么

基于 OpenStreetMap 数据，把区域按**环路 / 主干道 / 河流**切割成若干块，并把 POI 归类到对应区块。两类切割：
- **闭合环 (closed)**：环路/行政界 → 内/外
- **线性 (linear)**：主干道/河流 → 南/北 或 东/西

详细文档见 [README.md](README.md)，待办见 [TODO.md](TODO.md)。

---

## 接手前必读

### 1. 先跑一遍验证，确认环境正常

```bash
cd /Users/ghb/ZCodeProject/market_partition
source /Users/ghb/ZCodeProject/.venv/bin/activate

# 单元测试（<1s）
python -m pytest tests/test_split.py tests/test_classify.py -q

# 如果有 PBF 数据，跑集成测试（~80s）
python -m pytest tests/test_pbf.py -q

# 跑全 5 case agent loop（验证算法正确性）
python examples/agent_loop_all.py
```

如果单元测试不过 → 环境坏了，先修依赖。
如果 5 case 准确率低于 80% → 算法回归了，先排查。

### 2. 启动 Web 服务

```bash
export MARKET_PARTITION_PBF=$PWD/data/beijing-latest.osm.pbf
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000/ 验证前端能加载、API 能返回。

---

## 架构概览

```
用户意图                          GIS 确定性运算
─────────                        ─────────────
"五环路切内外"                    sources/pbf.py   ← 取路网/POI/边界
       ↓                          sources/osm.py   ← PBF优先,回退Overpass
  api/routes.py                   geometry/split.py ← 切割(含线性延长)
       ↓                          geometry/orient.py← 方位判定
  geometry 层                     geometry/classify.py ← POI归类
       ↓
  GeoJSON → Leaflet 前端

agent/tools.py ← 6个自描述工具,供 LLM agent loop 用
```

### 数据流

1. `OsmSource.get_region(place)` → 行政边界（PBF 离线 / Nominatim 在线）
2. `OsmSource.get_barrier_by_name(name, region)` → 合并路段 MultiLineString
3. `partition(region, barriers)` → SplitResult（切割后区块）
4. `classify_points(pois, regions)` → 每个POI归到哪个区块
5. `regions_to_geojson(result)` → 返回前端

---

## 关键模块速查

| 要改什么 | 看哪个文件 | 关键函数 |
|---------|----------|---------|
| 切割算法 | `geometry/split.py` | `partition()`, `_extend_line_to_boundary()`, `_has_meaningful_split()` |
| 方位标签 | `geometry/orient.py` | `label_in_out()`, `label_ns()`, `label_ew()` |
| POI归类 | `geometry/classify.py` | `classify_points()`, `tally_by_region()` |
| 取路网/POI/边界 | `sources/pbf.py` | `PbfSource._get_roads()`, `get_barrier_by_name()`, `get_region()` |
| 在线回退 | `sources/osm.py` | `OsmSource`（PBF优先，Overpass兜底） |
| API端点 | `api/routes.py` | `partition_endpoint()`, `_resolve_region()`, `_fetch_barrier()` |
| Agent工具 | `agent/tools.py` | `fetch_barrier`, `run_partition`, `reconstruct_ring`, `check_landmarks`, `render_result` |
| 前端 | `app/static/app.js` | `run()`, `render()`, `DEFAULT_BARRIERS` |

---

## 踩过的坑（必看，避免重犯）

### 坑 1：OSM 路名归一化

**问题**：用户输"五环路"，但 OSM 里主体路段叫"北五环"/"东五环"（不带"路"字）。"五环路"只匹配到 49 条辅路段，丢失 97% 数据。

**解法**：`pbf.py` 的 `get_barrier_by_name` 自动尝试后缀剥离——"五环路"匹配少时回退到"五环"。**不要假设用户知道 OSM 命名怪癖。**

### 坑 2：同名异义路段污染

**问题**："二环"匹配到密云区的"二环路"（距北京真二环 50km），几何被拉成覆盖半个北京。

**解法**：`_drop_spatial_outliers` 过滤——median 质心 + 0.5° 阈值。**注意阈值不能太小**：三环本身东西跨度 15km，阈值 0.05° 会把三环全杀掉。0.5° 才对。

### 坑 3：闭合环没切透（inside_area_frac≈0）

**问题**：OSM 环路数据有立交断口，polygonize 围不成环，buffer 切割只出 1 块（全标"外"）。

**解法**：用 `momepy.enclosures` 重建环内多边形（agent 工具 `reconstruct_ring`）。这是 urban morphology 标准库，比自己写 buffer 扫描可靠。

### 坑 4：线性屏障不延长就切不动

**问题**：长安街在 OSM 里只有 4km 数据，没横穿整个北京，buffer 切不透。

**解法**：`_extend_line_to_boundary` 沿主轴方向射射线，求与 region 边界交点，延长切割线。**人类说"按长安街切"是指方向概念线，必须延长到边界。**

### 坑 5：region 太小导致内外比例颠倒

**问题**：用小 bbox（116.30-116.55）切五环，bbox 大部分在五环内，导致"内"占 75%（实际应 4%）。

**解法**：region 必须用完整行政边界（geocode "北京市"），不能用手画的小 bbox。

### 坑 6：数字会骗人，必须看图

**问题**：只看面积/POI 数量"验证"，看不出切割对不对。五环的"回龙观错误"其实是期望表写错了（回龙观本来就五环外）。

**解法**：每个 case 必须：① 程序化地标核验 ② 渲染 PNG ③ 多模态视觉核验。光看数字会引入假阳性。

### 坑 7：polyogize 对断口环返回垃圾

**问题**：`polygonize(broken_ring)` 不返回空，而是返回 0.00003 的垃圾小多边形，被误当 ring_hull，导致全标"外"。

**解法**：polygonize 结果最大多边形 < region 1% 面积时，判定 polygonize 失败，回退 convex_hull。

---

## Agent Loop 使用方式

`agent/tools.py` 里的 6 个工具**任何 LLM 会话都能直接调**（不需要额外调度代码或 API key）。每个工具返回 `ToolResult`（data + diagnostics + warnings）。

典型循环：
```python
from market_partition.agent import fetch_barrier, run_partition, check_landmarks, render_result

r1 = fetch_barrier(src, "二环", region)
print(r1.warnings)  # 看 warnings 决定下一步

r2 = run_partition(region, r1.data, name="二环路", kind="closed")
if any("near zero" in w for w in r2.warnings):
    # 环没闭合 → 重建
    from market_partition.agent import reconstruct_ring
    r3 = reconstruct_ring(r1.data, region=region)
    # 用 r3.data 组装结果

r4 = check_landmarks(result, landmarks)
print(r4.diagnostics["accuracy"])  # 准确率

r5 = render_result(result, r1.data, Path("out.png"), landmarks)
# 然后 LLM 用视觉看 out.png 判断对不对
```

参考：`examples/agent_loop_demo.py`（二环完整循环）、`examples/agent_loop_all.py`（全 5 case）。

---

## 改代码时的检查清单

每次改完代码，必须过这些检查：

- [ ] `python -m pytest tests/ -q` 全绿（21 个测试）
- [ ] 改了 split/orient → 跑 `examples/agent_loop_all.py`，5 case 准确率不降
- [ ] 改了 API → curl 测一遍 `/api/partition`
- [ ] 改了前端 → 浏览器打开验证渲染
- [ ] 涉及切割逻辑 → **渲染 PNG + 多模态视觉核验**（不是只看数字）
- [ ] 改完重启服务（`pkill -f uvicorn; uvicorn app.main:app ...`），因为没开 --reload

---

## 常用命令

```bash
# 启动服务
export MARKET_PARTITION_PBF=$PWD/data/beijing-latest.osm.pbf
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 测试
python -m pytest tests/ -v

# 跑 demo
python examples/run_demo.py beijing_5ring
python examples/agent_loop_all.py

# 查 API 状态
curl http://localhost:8000/api/source
curl http://localhost:8000/api/health

# 清缓存（Overpass 的 SQLite 缓存）
python -c "from market_partition.sources.cache import OsmCache; OsmCache().clear()"
```

---

## 扩展新城市

1. 从 [Geofabrik](https://download.geofabrik.de) 下载目标省份的 `.osm.pbf`
2. 放到 `data/` 目录
3. 启动时 `export MARKET_PARTITION_PBF=$PWD/data/<province>-latest.osm.pbf`
4. `OsmSource` 自动用新 PBF，`get_region("上海市")` 等自动适配

**注意**：不同省份的 OSM 数据质量差异大。一二线城市好，偏远乡镇可能缺路名/边界。遇到数据问题先确认 PBF 里有没有，不要假设算法错。

---

## 不要做的事

- ❌ **不要自己设计 GIS 算法**——用 momepy / shapely / geopandas 的成熟 API。需要新功能先搜 GitHub/论文。
- ❌ **不要只看数字验证**——面积/POI 数看不出切割对不对，必须渲染图 + 视觉核验。
- ❌ **不要假设用户知道 OSM 命名**——"五环路"要自动回退"五环"，不能报"找不到"。
- ❌ **不要用小 bbox 测环路**——region 必须是完整行政边界，否则内外比例颠倒。
- ❌ **不要忽略 warnings**——agent 工具的 warnings 是判断信号，不是装饰。
