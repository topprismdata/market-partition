/* market_partition front-end
 *
 * Flow:  form → POST /api/partition → GeoJSON → render on Leaflet.
 * Three render layers: region polygons (outline only, no fill) + POI heatmap
 * (rainbow gradient, dense=red) + barrier line (red dashed) on top.
 * Center labels on each region carry the name + POI count.
 */

const DEFAULT_BARRIERS = [
  { name: "五环路", kind: "closed", extra_patterns: ["S50", "G4501"], orient_scheme: null },
];

const PALETTE = [
  "#4C78A8", "#F58518", "#54A24B", "#E45756",
  "#72B7B2", "#EECA3B", "#B279A2", "#FF9DA6",
];

let map;
let regionsLayer, pointsLayer, barrierLayer, labelLayer;
let activeRegions = null;  // last result, for inspection

function initMap() {
  map = L.map("map", { preferCanvas: true }).setView([39.9042, 116.4074], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "© OpenStreetMap",
  }).addTo(map);
}

// ---------------- barrier rows ----------------
function renderBarriers(list) {
  const root = document.getElementById("barrierList");
  root.innerHTML = "";
  list.forEach((b, i) => {
    const row = document.createElement("div");
    row.className = "barrier-row";
    row.innerHTML = `
      <input class="b-name"  type="text"  placeholder="路名(五环路)" value="${b.name || ""}">
      <select class="b-kind">
        <option value="closed" ${b.kind === "closed" ? "selected" : ""}>闭合环(内外)</option>
        <option value="linear" ${b.kind === "linear" ? "selected" : ""}>线性(南北/东西)</option>
      </select>
      <select class="b-orient" ${b.kind === "closed" ? "disabled" : ""}>
        <option value="">默认</option>
        <option value="ns"  ${b.orient_scheme === "ns" ? "selected" : ""}>南北</option>
        <option value="ew"  ${b.orient_scheme === "ew" ? "selected" : ""}>东西</option>
      </select>
      <button type="button" class="b-del" title="删除">×</button>
    `;
    row.querySelector(".b-kind").addEventListener("change", (e) => {
      list[i].kind = e.target.value;
      list[i].orient_scheme = null;
      renderBarriers(list);
    });
    row.querySelector(".b-name").addEventListener("input", (e) => {
      list[i].name = e.target.value;
    });
    row.querySelector(".b-orient").addEventListener("change", (e) => {
      list[i].orient_scheme = e.target.value || null;
    });
    row.querySelector(".b-del").addEventListener("click", () => {
      list.splice(i, 1);
      renderBarriers(list);
    });
    root.appendChild(row);
  });
}

function collectBarriers() {
  // Re-read from the DOM as source of truth.
  const rows = document.querySelectorAll("#barrierList .barrier-row");
  const out = [];
  rows.forEach((r) => {
    const name = r.querySelector(".b-name").value.trim();
    if (!name) return;
    const kind = r.querySelector(".b-kind").value;
    const orient = r.querySelector(".b-orient").value || null;
    out.push({ name, kind, orient_scheme: orient, extra_patterns: [] });
  });
  return out;
}

// ---------------- run ----------------
async function run() {
  const status = document.getElementById("status");
  const btn = document.getElementById("runBtn");
  btn.disabled = true;
  status.textContent = "⏳ 正在请求 OSM 并切割... (首次可能 30-60s)";
  status.className = "status busy";

  const body = {
    region: { place: document.getElementById("place").value.trim() || null },
    barriers: collectBarriers(),
    classify_points: document.getElementById("classifyPoints").checked,
    poi_tags: JSON.parse(document.getElementById("poiPreset").value),
    cache_bust: document.getElementById("cacheBust").checked,
  };
  const bufStr = document.getElementById("bufferDeg").value.trim();
  if (bufStr) body.buffer_deg = parseFloat(bufStr);
  const snapStr = document.getElementById("snapDeg").value.trim();
  if (snapStr) body.snap_deg = parseFloat(snapStr);

  if (body.barriers.length === 0) {
    status.textContent = "❌ 请至少添加一个切割要素";
    status.className = "status error";
    btn.disabled = false;
    return;
  }

  try {
    const resp = await fetch("/api/partition", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    render(data);
    status.textContent = `✅ 切割完成 — ${data.diagnostics.n_pieces} 块`;
    status.className = "status ok";
  } catch (e) {
    status.textContent = `❌ ${e.message}`;
    status.className = "status error";
    console.error(e);
  } finally {
    btn.disabled = false;
  }
}

// Compute a representative center [lat, lng] for a GeoJSON feature directly
// from its coordinates, avoiding lyr.getBounds() which can throw on some
// Leaflet/GeoJSON layer combinations.
function _featureCenter(feat) {
  const coords = feat.geometry.coordinates;
  const depth = Array.isArray(coords) ? _arrayDepth(coords) : 0;
  // Normalize to a flat list of [lng, lat] positions.
  let positions;
  if (depth <= 2) {
    positions = [coords];                 // single point [lng, lat]
  } else if (depth === 3) {
    positions = coords.flat();            // Polygon: [ [ [lng,lat], ... ] ] → [ [lng,lat], ... ]
  } else if (depth === 4) {
    positions = coords.flat(2);           // MultiPolygon
  } else {
    positions = JSON.parse(JSON.stringify(coords)).flat(Infinity);
  }
  let minLng = Infinity, maxLng = -Infinity, minLat = Infinity, maxLat = -Infinity;
  for (const [lng, lat] of positions) {
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  return [(minLat + maxLat) / 2, (minLng + maxLng) / 2];
}

function _arrayDepth(arr) {
  return Array.isArray(arr) ? 1 + _arrayDepth(arr[0]) : 0;
}

// ---------------- render ----------------
function render(data) {
  activeRegions = data;

  // Clear previous layers.
  if (regionsLayer) map.removeLayer(regionsLayer);
  if (pointsLayer) map.removeLayer(pointsLayer);
  if (barrierLayer) map.removeLayer(barrierLayer);
  if (labelLayer) map.removeLayer(labelLayer);

  // Region polygons — outline only, no fill, so the base map stays readable.
  const features = data.features;
  regionsLayer = L.geoJSON(features, {
    style: (feat) => {
      const idx = feat.properties.region_id ?? 0;
      return {
        color: PALETTE[idx % PALETTE.length],
        weight: 3,
        fillColor: PALETTE[idx % PALETTE.length],
        fillOpacity: 0,
      };
    },
    onEachFeature: (feat, lyr) => {
      const p = feat.properties;
      lyr.bindPopup(
        `<b>${p.label}</b><br>` +
        `区域 ${p.region_id}<br>` +
        `面积: ${p.area}<br>` +
        `POI 数: ${p.poi_count}`
      );
    },
  }).addTo(map);

  // Center labels per region — standalone divIcon markers in their own layer
  // group. Iterate the raw GeoJSON features directly (no Leaflet layer
  // dependency) and compute the center from the feature's own coordinates.
  labelLayer = L.layerGroup().addTo(map);
  data.features.forEach((feat) => {
    const p = feat.properties;
    const idx = p.region_id ?? 0;
    const color = PALETTE[idx % PALETTE.length];
    const center = _featureCenter(feat);
    L.marker(center, {
      keyboard: false,
      icon: L.divIcon({
        className: "region-label",
        html: `<span class="region-label-chip" style="background:${color}">${p.label}<br><small>${p.poi_count} POI</small></span>`,
        iconSize: [140, 44],
        iconAnchor: [70, 22],
      }),
    }).addTo(labelLayer);
  });

  // Barrier line on top in red.
  if (data.barrier) {
    barrierLayer = L.geoJSON(data.barrier, {
      style: { color: "#d62728", weight: 3, dashArray: "6 4" },
    }).addTo(map);
  }

  // POI → heatmap (rainbow gradient). Dense clusters glow red/yellow; the
  // base map remains visible everywhere. Single-point popups are lost by
  // design — the table + popup on each region carry the counts.
  if (data.points && data.points.features.length) {
    const latlngs = data.points.features.map((f) => {
      const [lng, lat] = f.geometry.coordinates;
      return [lat, lng, 0.5];
    });
    pointsLayer = L.heatLayer(latlngs, {
      radius: 18,
      blur: 22,
      maxZoom: 14,
      max: 1.0,
      minOpacity: 0.25,
      gradient: {
        0.0: "blue",
        0.25: "cyan",
        0.5: "lime",
        0.75: "yellow",
        1.0: "red",
      },
    }).addTo(map);
  }

  map.fitBounds(regionsLayer.getBounds(), { padding: [30, 30] });
  renderResultsPanel(data);
}

function renderResultsPanel(data) {
  const root = document.getElementById("results");
  root.innerHTML = "<h3>划分结果</h3>";
  const tbl = document.createElement("table");
  tbl.innerHTML = `
    <tr><th></th><th>#</th><th>标签</th><th>面积</th><th>POI 数</th></tr>
  `;
  data.features.forEach((f) => {
    const p = f.properties;
    const idx = p.region_id ?? 0;
    const color = PALETTE[idx % PALETTE.length];
    const tr = document.createElement("tr");
    // Color swatch ties each row to its map outline color.
    tr.innerHTML =
      `<td><span class="swatch" style="background:${color}"></span></td>` +
      `<td>${p.region_id}</td><td>${p.label}</td><td>${p.area}</td><td>${p.poi_count}</td>`;
    tbl.appendChild(tr);
  });
  root.appendChild(tbl);

  // Diagnostics block (for tuning advanced params).
  const d = data.diagnostics;
  const meta = document.createElement("div");
  meta.className = "diag";
  meta.innerHTML = `
    <strong>诊断</strong><br>
    切割块数: ${d.n_pieces}<br>
    buffer 宽度: ${d.buffer_deg?.toFixed(5) || "-"}°<br>
    标签: ${(d.labels || []).join(", ")}
  `;
  root.appendChild(meta);
}

// ---------------- bootstrap ----------------
window.addEventListener("DOMContentLoaded", () => {
  initMap();
  let barriers = DEFAULT_BARRIERS.map((b) => ({ ...b }));
  renderBarriers(barriers);
  document.getElementById("addBarrier").addEventListener("click", () => {
    barriers.push({ name: "", kind: "linear", orient_scheme: null, extra_patterns: [] });
    renderBarriers(barriers);
  });
  document.getElementById("runBtn").addEventListener("click", run);
});
