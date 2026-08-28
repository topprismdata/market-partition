# Data Provenance

本文档记录 `market-partition` 公开仓库涉及的所有数据来源、归属要求与排除项。

---

## 主要数据源

### OpenStreetMap (OSM)

- **来源**：OpenStreetMap contributors via [Geofabrik Downloads](https://download.geofabrik.de/) 与 [Overpass API](https://overpass-api.de/)
- **许可**：Open Database License (ODbL) 1.0
- **使用方式**：本地 `.osm.pbf` 文件（离线、快速、可复现）+ Overpass API 在线回退（受限流影响）
- **示例**：Beijing PBF 约 35 MB，可从 `https://download.geofabrik.de/asia/china/beijing-latest.osm.pbf` 下载

### Attribution 要求（OSM ODbL 第 4 节）

任何基于本仓库发布的衍生作品必须包含：

> "Map data © OpenStreetMap contributors, ODbL 1.0"

本仓库自身以及其默认 demo 输出（folium HTML、PNG 渲染图）均需保留此 attribution。

## 本地缓存

- OSM 解析后的路网、POI、行政边界在进程内缓存（首次解析约 12 秒，重复查询 < 0.1 秒）
- Overpass API 的查询结果可选择持久化到 `data/cache.sqlite`（见 `market_partition/sources/cache.py`）

## 故意**不**提交到仓库的内容

- `.osm.pbf` 文件（通过 `.gitignore` 排除）
- API key、token、cookie
- 任何企业 POI / 门店 / 客户数据
- 真实坐标的内部测试集

如需在生产环境中使用真实企业 POI 数据，请**不要**提交到本仓库；通过环境变量或独立配置文件注入。

## 默认测试数据

`examples/` 下所有 demo 脚本使用：

- 默认 Beijing 行政边界（来自 OSM admin level）
- 默认 POI 来自 OSM 自带 POI 节点（`amenity`, `shop`, `office` 等标签）

无任何合成/伪企业数据。

## 与客户/企业数据的关系

`market-partition` 公开仓库：

- **不包含**任何客户、企业或商业实体的真实 POI / 门店 / 营业地址数据
- **不包含**任何私有地理数据集
- **不存储**任何上传的文件

如需在客户项目中应用本工具，请通过：

1. 独立的私有数据适配层（不在本仓库内）
2. 客户自己提供的 PBF 或私有 GIS 数据源
3. 满足客户合规要求的 OSM 衍生数据

## 复现实验所需依赖

- Python ≥ 3.10
- 依赖（见 `pyproject.toml`）：osmnx, geopandas, shapely, folium, fastapi, uvicorn, pyrosm, momepy, matplotlib
- 约 100 MB 磁盘空间（含 OSM PBF 缓存）

## 联系 / 报告数据问题

如果在使用 OSM 数据时发现错误：

- 直接在 [OpenStreetMap](https://www.openstreetmap.org/) 上修正（推荐）
- 在本仓库 issue 中报告（仅限本仓库代码问题，不处理上游 OSM 数据问题）
