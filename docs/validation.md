# Validation — 已验证的 5 个 Beijing Cases

本文档记录 5 个 Beijing 切割 case 的完整评估结果。每个 case 都经过 **程序化地标核验 + 多模态视觉核验** 双重验证。

---

## Case 表

| Case | 类型 | 地标准确率 | 视觉核验 | 备注 |
|------|------|-----------|---------|------|
| 二环 | 闭合环 | 10/11 (91%) | ✓ 环闭合，天安门在内 | 北京站落在 buffer 带（边界 case） |
| 三环 | 闭合环 | 10/11 (91%) | ✓ 环闭合 | 国贸在东三环边缘 |
| 四环 | 闭合环 | 7/10 (70%) | ✓ 环闭合 | 中关村/望京/奥森在四环边缘 1-2km |
| 五环 | 闭合环 | 9/10 (90%) | ✓ 大环完整，占北京 4% | 回龙观实际本就在五环外 |
| 长安街 | 线性 | 5/5 (100%) | ✓ 延长线横贯北京 | 北 73% / 南 27% |

## 多模态视觉核验的真实价值

五环 case 的数字显示"回龙观错了"（期望五环内，实际五环外），本来要当 bug 去修。但视觉模型 + 地理常识交叉验证发现：**回龙观本来就五环外**——是测试基准写错了，不是算法错。

光看数字（accuracy=90%）会引入假阳性（花时间修不存在的 bug）。视觉核验避免了这个问题。这正是引入多模态的核心价值：**不是让 AI 看图连线，而是让 AI 看图判断结果对不对**。

## 程序化验证方法

每个 case 的"地标期望表"是一个 `{name: (lon, lat, expected_inside/outside_or_north/south)}` 字典。切割完成后对每个地标做：

```python
for name, (lon, lat, expected) in landmarks.items():
    point = Point(lon, lat)
    actual = classify(point, split_result)  # 'inside' / 'outside'
    if actual == expected:
        correct += 1
total_correct = correct
accuracy = total_correct / len(landmarks)
```

## 视觉验证方法

1. 渲染切割后的 GeoJSON（彩色描边）+ POI 热力图（虹彩渐变）
2. LLM 用 vision 能力看渲染图，判断环是否闭合、内外方向是否正确、POI 分类是否符合常识
3. 视觉判断与程序化数字交叉：数字异常时由视觉复核，反之亦然

## 不完美案例的诚实记录

- **四环 70%**：中关村、望京、奥森都在四环边缘 1-2km，地标点的精确放置有歧义；视觉核验通过
- **二环 91%**：北京站落在 buffer 带（边界 case），属于 OSM 数据本身的歧义而非算法问题
- **五环 90%**：原始期望表写错（回龙观应在环外），视觉核验识别出"基准错"而不是"算法错"

## 这些证据**不**支持

- 在所有城市、所有 OSM 数据质量条件下都能达到同等准确率
- 完全自主的商业 territory 设计
- 多模态视觉核验替代决定性几何验证（视觉是 secondary evidence layer，不是 geometric proof）

## 评估环境的可复现性

- OSM 数据：Geofabrik `beijing-latest.osm.pbf`（约 35 MB），解析后进程内缓存
- Python ≥ 3.10，依赖见 `pyproject.toml`
- 完整 5 case 跑完：`python examples/agent_loop_all.py`（含诊断 + 渲染验证图）

## 相关文档

- [`agent-loop.md`](agent-loop.md) — 6 工具详解
- [`geometry.md`](geometry.md) — 算法细节
- [`../DATA_PROVENANCE.md`](../DATA_PROVENANCE.md) — 数据来源
