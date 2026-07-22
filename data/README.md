# Data Directory

This directory holds local `.osm.pbf` files. **These files are NOT committed to git** (see `.gitignore`) — they are large (~35MB each) and must be downloaded separately.

## Download

### Beijing (current development data)

```bash
curl -L --retry 5 -o data/beijing-latest.osm.pbf \
  https://download.geofabrik.de/asia/china/beijing-latest.osm.pbf
```

Source: [Geofabrik Beijing](https://download.geofabrik.de/asia/china/beijing.html)

### Other provinces / cities

Browse [Geofabrik](https://download.geofabrik.de) for other regions. Download the `.osm.pbf` format (NOT `.shp` or `.gpkg` — only `.osm.pbf` preserves the full OSM name tags that the tool relies on).

## Usage

```bash
export MARKET_PARTITION_PBF=$PWD/data/beijing-latest.osm.pbf
uvicorn app.main:app --port 8000
```

Or pass the path directly in Python:
```python
from market_partition.sources.osm import OsmSource
src = OsmSource(pbf_path="data/beijing-latest.osm.pbf")
```

## Why .osm.pbf (not .shp / .gpkg)

Only `.osm.pbf` preserves the full original OSM tags (`name`, `ref`, `highway`). The tool matches roads by name (e.g. "五环" matches "北五环"/"东五环"/...). Geofabrik's `.shp`/`.gpkg` derivatives normalize the name field, which breaks this matching and loses road segments.
