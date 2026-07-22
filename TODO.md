# 待完成工作 (Roadmap)

## 当前状态

已验证的切割能力：
- ✅ 闭合环切割（二/三/四/五环，北京全市级）
- ✅ 线性切割（长安街，含延长到边界）
- ✅ POI 点分类（餐饮/商店等归到切割后区块）
- ✅ 本地 PBF 离线数据源（路网 + POI + 行政边界）
- ✅ Agent loop 自验证工具链（6 工具 + 多模态核验）
- ✅ Web 前端（Leaflet 交互式）+ FastAPI 后端

---

## 核心待完成（用户明确要求）

### 1. 基于行政区划的组合切割

**需求**：把多个行政区划作为一个整体 region 进行切割。例如：
- "把朝阳区 + 海淀区 + 丰台区作为一个整体，按四环路切内外"
- "把北京东部的 3 个区合并，按某高速切两部分"

**当前限制**：`region` 只支持单个 place（一个行政边界）或显式 polygon。多 place 需要手动算 union。

**实现方案**：
```
RegionSpec 扩展:
  region: {places: ["朝阳区","海淀区","丰台区"]}  ← 新增多place

OsmSource.get_region 扩展:
  def get_region_combined(places: list[str]) -> Polygon:
      polys = [self.get_region(p) for p in places]
      return unary_union(polys)
```

**工作量**：小。API 模型 + osm.py 加一个 union 方法 + 前端加多选。约 1-2 小时。

**验证**：朝阳区+海淀区合并后按四环切，验证地标（望京应在合并区内的四环外）。

---

### 2. 在行政区划内切割（区县 / 乡镇级）

**需求**：在区县或乡镇级别的小区域内切割。例如：
- "在海淀区内按中关村大街切东西"
- "在某某乡镇内按某省道切南北"

**当前限制**：所有测试在北京市级（admin_level=4）跑通，区县级（admin_level=6）和乡镇级（admin_level=8）未验证。

**需要验证的问题**：
1. **PBF 行政边界完整性**：区县/乡镇边界在 OSM 里是否完整闭合？（偏远乡镇可能缺失）
2. **小区域内主干道数据覆盖度**：乡镇级的主干道（省道/县道）在 OSM 里是否有 name tag？
3. **buffer 精度**：小区域切割需要更窄的 buffer（乡镇面积小，宽 buffer 会吃掉大部分区域）

**实现方案**：
```python
# 已有: get_region 支持任意 place 名 → PBF admin boundary 查询
# 海淀区 → admin_level=6 的边界 (已确认 PBF 里有)
src.get_region("海淀区")  # 已能工作

# 需要验证: 在小区域内切割的效果
region = src.get_region("海淀区")
barrier = src.get_barrier_by_name("中关村大街", region)
result = partition(region, [barrier])
```

**工作量**：主要在验证 + 调参，代码改动小。约半天。

**风险**：乡镇级数据可能不完整，需要 fallback 策略（如手动提供 polygon）。

---

## 其他待优化（优先级较低）

### 3. 多层切割（先环后路）

"先把北京按五环切内外，再把五环内按长安街切南北" → 4 块。

当前 `partition` 支持多 barrier 顺序切割，但方位标签只保留最后一层。需要改为分层标签（如"五环内-长安街北"）。

### 4. 河流切割

河流（waterway=river）作为线性屏障，逻辑同主干道。但河流形状曲折，延长逻辑需要适配（不能简单直线延长）。需要用河流的 `LineString` 本身做切割线，不走延长。

### 5. 切割结果导出

- 导出为 GeoJSON 文件（当前只有 API 返回）
- 导出为 Shapefile（GIS 工具兼容）
- 导出切割后区块的 POI 列表 CSV

### 6. 缓存可视化优化

前端渲染大区域（如整个北京 + 上万 POI）会卡。需要：
- 服务端渲染 PNG 返回（而非传 GeoJSON 给前端画）
- POI 聚合（cluster）减少前端渲染压力

### 7. 更多城市的 PBF 支持

当前只有北京 PBF。其他城市需从 Geofabrik 下载对应省级 PBF。`OsmSource` 已支持传任意 pbf_path，只需扩展数据准备。

---

## 已知问题

| 问题 | 影响 | 临时方案 |
|------|------|---------|
| 四环边界地标（中关村/望京）误差 1-2km | 边界 POI 归类可能错 | 用 momepy.enclosures 替代 buffer 切割（待实现） |
| 闭合环切割偶发小碎片（0.2%-0.4%） | 多出无意义小区块 | 已有 MIN_AREA_FRAC 过滤，可调阈值 |
| 二环路 OSM 数据碎片严重 | 需要 momepy 重建 | 已用 reconstruct_ring 解决 |
| 长安街延长用直线假设 | 弯曲道路延长不精确 | 对直线道路够用；弯曲路需 future work |
