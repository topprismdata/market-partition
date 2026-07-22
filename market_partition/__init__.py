"""Market Partition — split geographic regions by ring roads, main roads, rivers.

A two-layer tool:
  - LLM/API layer translates intent (place + barrier name) into OSM queries.
  - GIS layer (osmnx + shapely + geopandas) does the deterministic geometric splitting.

Two barrier kinds are supported:
  - "closed": a closed ring (ring road, administrative boundary) → inside / outside.
  - "linear": an open line (main road, river) → north/south or east/west.
"""

__version__ = "0.1.0"
