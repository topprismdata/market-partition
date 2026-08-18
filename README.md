# Market Partition

**Deterministic spatial partitioning with agentic semantic
interpretation and visual verification.**

`WORLD MODEL` · `APPLIED RESEARCH` · `PUBLIC OSM DATA` · `MIT`

> **World-model question:** How can a business-defined market boundary
> such as "inside the Fifth Ring", "north of Chang'an Avenue", or
> "either side of this corridor" be converted into deterministic,
> reproducible geometry?

Part of **TopPrism Business World Modeling**. Geo is one dimension of
the business world model; this repository focuses specifically on
translating human spatial intent into auditable geometric structure.

------------------------------------------------------------------------

## Why this exists

Commercial territories are often defined in human language:

-   inside / outside a ring road;
-   north / south of a major corridor;
-   east / west of a river;
-   areas separated by a natural or transportation barrier.

People understand these boundaries immediately. Enterprise systems need
explicit polygons and deterministic point classification.

The core design principle is:

> **LLMs interpret intent and inspect results. GIS performs
> deterministic geometry.**

The agent does not "draw the answer" by intuition. It selects and
supervises tools; the geometry remains reproducible.

------------------------------------------------------------------------

## What this project does

``` text
Natural-language spatial intent
          ↓
Semantic interpretation
          ↓
OSM road / boundary retrieval
          ↓
Deterministic geometry
          ↓
Region partition
          ↓
POI / outlet classification
          ↓
Diagnostics + rendered map
          ↓
Agent / multimodal verification
```

### LLM / Agent layer

-   interprets human intent;
-   maps phrases such as "inside Fifth Ring" to operations;
-   reads diagnostics and warnings;
-   decides whether to retry with another strategy;
-   uses rendered results for visual verification.

### GIS layer

-   retrieves OSM geometry;
-   normalizes and filters road segments;
-   reconstructs fragmented rings;
-   extends linear barriers to region boundaries;
-   partitions polygons;
-   classifies POIs / outlets;
-   produces GeoJSON and deterministic diagnostics.

------------------------------------------------------------------------

## Why the separation matters

``` text
LLM / Agent                         GIS
────────────                        ───────────────
understand intent                   fetch geometry
choose operation                    reconstruct ring
interpret diagnostics               split polygon
inspect rendered result             classify points
decide retry / accept               produce GeoJSON
```

This avoids two common failure modes:

1.  asking a language model to perform geometry it cannot reliably
    reproduce;
2.  running deterministic GIS code without a reasoning layer that
    notices semantically wrong results.

------------------------------------------------------------------------

## Evidence

The current repository reports five Beijing validation cases using
**programmatic landmark checks plus multimodal visual inspection**.

  ------------------------------------------------------------------------
  Case             Type             Programmatic landmark Visual check
                                                   result 
  ---------------- ---------------- --------------------- ----------------
  Second Ring      closed ring                10/11 (91%) ring visually
                                                          closed

  Third Ring       closed ring                10/11 (91%) ring visually
                                                          closed

  Fourth Ring      closed ring                 7/10 (70%) ring visually
                                                          closed

  Fifth Ring       closed ring                 9/10 (90%) large ring
                                                          visually
                                                          complete

  Chang'an Avenue  linear barrier              5/5 (100%) extended line
                                                          spans region
  ------------------------------------------------------------------------

The imperfect landmark scores should remain visible. Some "errors" are
genuinely ambiguous edge cases, and at least one expected landmark label
was found to be wrong during visual / geographic review.

### What this evidence supports

-   fragmented OSM ring roads can often be reconstructed into useful
    commercial boundaries;
-   deterministic geometry and agent verification can be combined in one
    workflow;
-   visual verification can reveal errors in the test expectation
    itself, not only in the geometry;
-   the method works across both closed-ring and linear-barrier examples
    in the published cases.

### What it does not support

-   universal accuracy across all cities or OSM data quality conditions;
-   fully autonomous commercial territory design;
-   a claim that multimodal inspection replaces deterministic geometric
    validation.

------------------------------------------------------------------------

## Architecture

``` text
┌──────────────────────────────────────────────────────┐
│ HUMAN INTENT                                         │
│ “split Beijing by the Fifth Ring”                    │
└──────────────────────────┬───────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────┐
│ AGENT SEMANTIC LAYER                                 │
│ intent → barrier type → orientation → tool choice    │
└──────────────────────────┬───────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────┐
│ DETERMINISTIC GEO LAYER                              │
│ OSM → normalize → reconstruct / extend → split       │
└──────────────────────────┬───────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────┐
│ BUSINESS WORLD STRUCTURE                             │
│ polygons + classified POIs / outlets + diagnostics  │
└──────────────────────────┬───────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────┐
│ VERIFICATION                                         │
│ landmark checks + rendered-map inspection            │
└──────────────────────────────────────────────────────┘
```

------------------------------------------------------------------------

## Where it fits at TopPrism

``` text
Raw geographic reality
roads · rivers · boundaries · POIs
             ↓
Market Partition
             ↓
Business spatial structure
             ↓
Business World Model
             ↓
territory / opportunity / visit / network decisions
```

This repository is **not TopPrism's entire geo platform**. It is one
focused mechanism for turning geographic structure into machine-usable
business context.

------------------------------------------------------------------------

## Quick start

``` bash
git clone https://github.com/topprismdata/market-partition.git
cd market-partition
pip install -e .
```

Recommended: use a local `.osm.pbf` file for reproducible offline runs.

``` bash
export MARKET_PARTITION_PBF=$PWD/data/beijing-latest.osm.pbf
uvicorn app.main:app --port 8000
```

Example cases:

``` bash
python examples/run_demo.py beijing_5ring
python examples/run_demo.py beijing_changan
python examples/agent_loop_all.py
```

------------------------------------------------------------------------

## Agent loop

The agent loop should be presented as a **supervisory verification
loop**, not as a replacement for GIS.

``` text
tool result
   ↓
diagnostics + warnings
   ↓
agent judgment
   ├── accept
   ├── retry
   ├── reconstruct
   └── render for visual check
```

The six current tools can remain documented in `docs/agent-loop.md`; the
README only needs the conceptual flow.

------------------------------------------------------------------------

## Core geometry

### Closed barriers

For ring roads and other closed barriers:

1.  normalize road-name matching;
2.  remove geographically implausible same-name segments;
3.  adaptively buffer fragmented ways;
4.  reconstruct an enclosure when necessary;
5.  classify inside / outside.

### Linear barriers

For major roads, rivers, or conceptual dividing corridors:

1.  infer the principal direction;
2.  extend the line to the region boundary;
3.  create a narrow splitting band;
4.  split the polygon;
5.  label sides by orientation.

Detailed geometry belongs in `docs/geometry.md`.

------------------------------------------------------------------------

## Data provenance

Recommended `DATA_PROVENANCE.md` should document:

-   OpenStreetMap / Geofabrik sources;
-   local PBF versus Overpass usage;
-   OSM license / attribution requirements;
-   which datasets are intentionally not committed;
-   whether any enterprise POI / outlet data used in downstream projects
    is excluded from this public repository.

The public repository should remain runnable with public or synthetic
data.

------------------------------------------------------------------------

## Boundaries & limitations

-   OSM road naming and topology vary by city.
-   Fragmented ring geometry may require reconstruction.
-   Edge landmarks can make point-based validation ambiguous.
-   Visual verification is a secondary evidence layer, not a geometric
    proof.
-   Business territory design usually needs additional demand, capacity,
    organizational, and commercial constraints beyond geometry.

------------------------------------------------------------------------

## Recommended repository cleanup

Move long-form material out of the README:

``` text
docs/
├── agent-loop.md
├── geometry.md
├── validation.md
├── data-provenance.md
└── images/
```

Keep the README focused on:

> problem → architecture → evidence → boundaries → quick start.

------------------------------------------------------------------------

## TopPrism metadata

``` yaml
topprism:
  purpose: world-model
  capability: spatial-partitioning
  platform_layer: business-world-model
  maturity: applied-research
  evidence:
    type: public-real-world-data
    source: OpenStreetMap
    validation: programmatic-plus-visual
  product_context:
    - market-definition
    - territory-design
    - spatial-intelligence
```

## License

MIT. Observe OpenStreetMap attribution and applicable upstream data
licenses.
