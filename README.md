# 市场划分工具 (Market Partition)

基于 OpenStreetMap 数据自动把区域按**环路 / 主干道 / 河流**切割成若干块，并把区域内的 POI / 门店归类到对应区块。

人类做这件事很简单（沿环路画线、沿主干道切南北），本工具让机器也能做。

---

## 两类切割

| 类型 | 屏障 | 例子 | 切割结果 |
|------|------|------|---------|
| **闭合环 (closed)** | 环路、行政界 | 北京五环路 | 内 / 外 |
| **线性 (linear)** | 主干道、河流 | 长安街 | 南 / 北 或 东 / 西 |

### 闭合环切割

真实 OSM 里一条环路是 200-2000 条断开的 way 段（立交桥、匝道导致断裂）。算法：
1. 按 name 聚合 way 段（"五环路"自动回退匹配"五环"→"北五环/东五环/..."）
2. 空间过滤远郊同名异义路段（tol=0.5°，只杀 50km 外的同名路）
3. 自适应 buffer（从 0.0001° 扫到 0.005°，选最窄能切≥2 块且有实质次大块的宽度）
4. 方位判定用 `representative_point` + polygonize 多边形（失败回退 convex_hull）
5. 环未闭合时用 **momepy.enclosures** 重建环内多边形

### 线性切割

主干道（如长安街）在 OSM 里只有城市中心的一小段，但人类说"按长安街切北京"是指这条路的**方向概念线**。算法：
1. 取道路主轴方向（最长段的首尾向量）
2. **沿主轴方向向两端延长射线，求射线与 region 边界的交点**
3. 把交点拼到原线上，让切割线横贯整个区域
4. 用延长后的线 + buffer 带 切割
5. 按法线轴投影判南/北或东/西

---

## 它不是"AI 看图"

设计思路是两层分离：

```
LLM/Agent 层（语义+判断）        GIS 层（确定性几何运算）
─────────────────────           ──────────────────────────
"五环路" → OSM 查询参数          osmnx/pyrosm 取数
"南北"   → 方位方案              shapely 切割 + 延长
切割后 → 看图判断对不对          momepy.enclosures 重建环
  ↓                              geopandas 管理
  多模态视觉自检                  folium/Leaflet 可视化
```

AI 的价值在**语义→操作映射**（理解"五环"指哪条路、"南北"是哪个方向）和**多模态自检**（渲染切割图后用视觉判断结果是否合理），不在视觉识别道路。

---

## 快速开始

### 1. 依赖

```bash
source /Users/ghb/ZCodeProject/.venv/bin/activate
pip install -e .   # 或手动装: osmnx geopandas shapely folium fastapi uvicorn pyrosm momepy matplotlib
```

### 2. 用本地 PBF（推荐：离线、快、无网络限流）

```bash
mkdir -p data
curl -L --retry 5 -o data/beijing-latest.osm.pbf \
  https://download.geofabrik.de/asia/china/beijing-latest.osm.pbf
```

PBF 提供**全部三类数据**：路网（barrier）、POI（点分类）、行政边界（region geocode），完全离线。

**为什么选 `.osm.pbf` 而非 `.shp` / `.gpkg`**：只有 `.osm.pbf` 保留完整的原始 OSM tag（`name`/`ref`/`highway`），算法靠 `name` 字段模糊匹配道路（"五环" 匹配 "北五环/东五环/..."），Geofabrik 的 shp/gpkg 衍生品会把名字规整掉导致丢路段。

### 3. Web 应用（交互式）

```bash
export MARKET_PARTITION_PBF=$PWD/data/beijing-latest.osm.pbf
uvicorn app.main:app --port 8000
```

打开 http://localhost:8000/ —— 左侧填地名 + 切割要素，右侧 Leaflet 地图实时展示切割结果。

- **区域**：填地名（"北京市"、"海淀区"）。PBF 模式下从本地 admin_level 边界取，无需网络。
- **切割要素**：选 `闭合环(内外)` 或 `线性(南北/东西)`，填路名（"五环路"、"长安街"）。
- **POI 分类**：勾选后自动拉 POI 并归类到切割后的区块。

### 4. CLI Demo

```bash
python examples/run_demo.py beijing_5ring     # 五环切内外
python examples/run_demo.py beijing_changan   # 长安街切南北
python examples/agent_loop_all.py             # agent loop 跑全部5个case
```

---

## Agent Loop（自验证工具链）

参考 [LLM-Geo](https://giscience.psu.edu/llm-geo-an-open-source-autonomous-gis-prototype/)（自验证 GIS）和 [GISclaw](https://arxiv.org/abs/2603.26845)（LLM 核 + Python 沙箱）设计。**核心思想：工具不是哑函数，每个工具返回诊断信号，让 LLM 判断下一步。**

```
┌──────────────────────────────────────────────────┐
│  LLM Agent（判断核）                              │
│  每轮: 看上一轮工具的诊断+警告 → 决定下一步       │
└──────────────┬───────────────────────────────────┘
               │ 调用
┌──────────────▼───────────────────────────────────┐
│  6 个自描述工具 (market_partition/agent/tools.py)│
│                                                  │
│  fetch_barrier    → 诊断: 路段数, 中心点分散度   │
│  run_partition    → 诊断: 块数, inside_area_frac │
│  reconstruct_ring → momepy.enclosures 重建环     │
│  check_landmarks  → 准确率, 哪些地标错了         │
│  render_result    → PNG 供视觉检查               │
│  visual_check     → 多模态自检(调视觉模型看图)   │
└──────────────────────────────────────────────────┘
```

**每个工具返回 `ToolResult`**，包含 `data` + `diagnostics` + `warnings`。LLM 靠 warnings 判断要不要换策略。例如：
- `run_partition` 警告 `inside_area_frac≈0` → LLM 决定调 `reconstruct_ring`
- `check_landmarks` 报某地标错 → LLM 决定渲染图 + 视觉核验

运行演示：`python examples/agent_loop_demo.py`（二环最难 case 的完整 agent loop）。

---

## 已验证的 5 个 Case

每个 case 都经过：程序化地标核验 + 多模态视觉核验。

| case | 类型 | 准确率 | 视觉核验 | 备注 |
|------|------|--------|---------|------|
| 二环 | closed | 10/11 | ✓ 环闭合，天安门在内 | 北京站是边界 case（落 buffer 带） |
| 三环 | closed | 10/11 | ✓ 环闭合 | 国贸是东三环边界 case |
| 四环 | closed | 7/10 | ✓ 环闭合 | 中关村/望京/奥森在四环边缘 |
| 五环 | closed | 9/10 | ✓ 大环完整，占 4% | 回龙观实际本就五环外（期望表曾写错） |
| 长安街 | linear | 5/5 | ✓ 延长线横贯北京 | 北 73% / 南 27% |

**多模态核验的最大价值**：五环 case 数字显示"回龙观错了"，本来要当 bug 修，但视觉模型 + 地理常识发现回龙观本来就五环外——**是测试基准错了，不是算法错**。光看数字会引入假阳性。

---

## 项目结构

```
market_partition/
├── market_partition/
│   ├── sources/        # 数据层（双源：本地 PBF + 在线 Overpass）
│   │   ├── osm.py      #   OsmSource：PBF 优先，回退 Overpass
│   │   ├── pbf.py      #   PbfSource：本地 .osm.pbf（路网+POI+行政边界）
│   │   └── cache.py    #   Overpass 结果的 SQLite 缓存
│   ├── geometry/       # 核心算法
│   │   ├── split.py    #   切割(closed/linear) + 线性延长到边界
│   │   ├── classify.py #   点分类（POI 归到区块）
│   │   └── orient.py   #   方位判定（内/外、南/北、东/西）
│   ├── agent/          # Agent loop 工具链（自验证）
│   │   └── tools.py    #   6 个自描述工具 + ToolResult
│   ├── api/            # FastAPI 路由 + pydantic schema
│   └── viz/            # GeoJSON 输出
├── app/
│   ├── main.py         # FastAPI 入口
│   └── static/         # Leaflet.js 单页前端
├── data/               # 本地 .osm.pbf（gitignore，需自行下载）
├── tests/              # 单元测试 + PBF 集成测试
├── examples/
│   ├── run_demo.py     # CLI demo（五环 + 长安街）
│   ├── agent_loop_demo.py    # 二环 agent loop 演示
│   ├── agent_loop_all.py     # 全 5 case agent loop
│   └── verify_cases.py       # 批量验证 + 渲染
└── pyproject.toml
```

---

## API

### POST `/api/partition`
```json
请求:
{
  "region": {"place": "北京市"},
  "barriers": [{"name": "五环路", "kind": "closed"}],
  "classify_points": true,
  "poi_tags": {"amenity": ["restaurant","cafe"]}
}
响应: GeoJSON FeatureCollection（切割区块 + 分类后的 POI）
```

### GET `/api/source` — 当前数据源（pbf / overpass）
### GET `/api/health` — 健康检查
### GET `/api/cache/stats` — 缓存命中

---

## 测试

```bash
python -m pytest tests/ -v
```

- 单元测试（合成几何，离线）：split / classify / orient
- PBF 集成测试（真实北京数据）：无 PBF/pyrosm 时自动 skip

---

## 待完成工作（Roadmap）

详见 [TODO.md](TODO.md)。两项核心扩展：

1. **基于行政区划的组合切割** — 例如"把朝阳区+海淀区+丰台区作为一个整体区域，按某环路切割"。当前 `region` 只支持单个 place 或 polygon，需要支持多 place 合并（union of admin boundaries）。

2. **在行政区划内切割（区县/乡镇级）** — 例如"在海淀区内按某主干道切南北"。当前测试都在北京市级，需要验证 PBF 里 admin_level=6（区）/7（镇）边界的完整性，以及乡镇级主干道数据的覆盖度。

---

## 设计原则（踩过的坑）

1. **不要自己设计 GIS 算法**——用成熟库。`momepy.enclosures` 重建环多边形比手写 buffer 扫描可靠 100 倍；shapely 的射线-边界求交比手画延长线可靠。
2. **数字会骗人，必须看图**——面积/POI 数量看不出"切割对不对"，必须渲染图 + 多模态视觉核验。
3. **测试基准也会错**——五环的"回龙观错误"其实是期望表写错了，视觉核验才发现。
4. **OSM 命名要归一化**——"五环路"匹配不到"北五环"，必须自动回退后缀。
5. **线性屏障必须延长**——长安街只有 4km 数据，人类语义是"沿这个方向切到底"。
