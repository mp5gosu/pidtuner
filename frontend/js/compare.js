// Step-Response tab: overlay consensus curves of selected sessions plus
// per-axis latency bars and a metrics table. Shows only logs uploaded in
// this browser session; the server holds uploads ephemerally and isolated
// per user (see routes_logs / session_store), never shared across tabs.

import { getStepResponse, renameSession } from "./api.js";
import { createChart, makeMaximizable, closeMaximized } from "./charts/uplotSetup.js";
import { makeSyncGroup } from "./charts/zoomPlugin.js";
import { persistCompare } from "./persist.js";
import { esc } from "./util.js";

const AXES = ["roll", "pitch", "yaw"];

// Validated categorical palette (dark mode) - see README/dataviz notes.
// Colors follow the session entity while selected, never its list position.
const PALETTE = ["#3987e5", "#199e70", "#c98500", "#008300",
                 "#9085e9", "#e66767", "#d55181", "#d95926"];

const uploadedLogs = [];   // logs uploaded in this browser session, in order
const selected = new Map(); // key -> {label, color, data}; insertion = selection order
let charts = [];
let toastFn = (msg) => console.warn(msg);

// Config panel starts collapsed to differences only; persisted across reloads.
let cfgShowAll = localStorage.getItem("pidtuner-cfg-showall") === "1";

export function initCompare(toast) {
  toastFn = toast;
}

// Remember which logs this browser uploaded and which sessions are compared, so
// an accidental reload re-attaches instead of losing everything. Nothing is
// deleted on tab close: the server reaps a log after DATA_TTL_MIN of inactivity
// and open tabs keep theirs alive via the keepalive heartbeat (see main.js).
function saveState() {
  persistCompare(uploadedLogs.map((l) => l.log_id), [...selected.keys()]);
}

// log_ids uploaded in this browser session, for the keepalive heartbeat.
export function uploadedLogIds() {
  return uploadedLogs.map((l) => l.log_id);
}

function sessionKey(logId, sid) {
  return `${logId}:${sid}`;
}

function defaultSessionLabel(log, sid) {
  return `${log.filename.replace(/\.(bbl|bfl|txt|log)$/i, "")} S${sid + 1}`;
}

// Custom name if the user set one, else the filename-derived default.
function makeLabel(log, sid) {
  const s = log.sessions.find((x) => x.session_id === sid);
  return s?.name || defaultSessionLabel(log, sid);
}

// Raw Betaflight header block for a session, for the config comparison table.
function sessionHeaders(log, sid) {
  return log.sessions.find((s) => s.session_id === sid)?.headers_raw ?? {};
}

function freeColor() {
  const used = new Set([...selected.values()].map((s) => s.color));
  return PALETTE.find((c) => !used.has(c)) ?? null;
}

export function registerUploadedLog(index) {
  if (!uploadedLogs.some((l) => l.log_id === index.log_id)) {
    uploadedLogs.push(index);
    saveState();
  }
}

// Programmatic selection (used after upload to auto-show the new log).
export async function selectForCompare(logId, sid) {
  const log = uploadedLogs.find((l) => l.log_id === logId);
  const key = sessionKey(logId, sid);
  if (!log || selected.has(key)) return;
  const color = freeColor();
  if (!color) return; // palette full - user picks manually instead
  const data = await getStepResponse(logId, sid);
  selected.set(key, {
    label: makeLabel(log, sid), color, data,
    headers: sessionHeaders(log, sid),
  });
  saveState();
  refreshUI();
}

// Re-attach after a page reload to the logs still alive on the server.
// `indexes` are freshly-fetched index objects (already confirmed alive);
// `selectedKeys` are the sessions that were being compared. Anything the server
// has since reaped, or that no longer fits the 8-colour palette, is dropped.
export async function restoreCompare(indexes, selectedKeys) {
  for (const index of indexes) registerUploadedLog(index);
  for (const key of selectedKeys) {
    if (selected.has(key)) continue;
    const sep = key.lastIndexOf(":");
    const logId = key.slice(0, sep);
    const sid = parseInt(key.slice(sep + 1), 10);
    const log = uploadedLogs.find((l) => l.log_id === logId);
    if (!log || !log.sessions.some((s) => s.session_id === sid)) continue;
    const color = freeColor();
    if (!color) break;
    try {
      const data = await getStepResponse(logId, sid);
      selected.set(key, {
        label: makeLabel(log, sid), color, data,
        headers: sessionHeaders(log, sid),
      });
    } catch { /* reaped or failed - skip */ }
  }
  saveState();
  refreshUI();
}

function refreshUI() {
  const picker = document.getElementById("compare-picker");
  const chartsEl = document.getElementById("compare-charts");
  if (picker) renderPicker(picker);
  if (chartsEl) renderCharts(chartsEl);
}

export function renderCompareTab() {
  refreshUI();
}

function renderPicker(pickerEl) {
  pickerEl.innerHTML = "";
  if (!uploadedLogs.length) {
    pickerEl.innerHTML =
      "<p class='hint'>No log uploaded yet. After upload the longest session " +
      "appears here automatically.</p>";
    return;
  }

  for (const log of uploadedLogs) {
    const group = document.createElement("div");
    group.className = "picker-log";

    const head = document.createElement("div");
    head.className = "picker-log-head";
    const title = document.createElement("span");
    title.className = "picker-log-name";
    title.textContent = log.filename;
    head.appendChild(title);
    group.appendChild(head);

    for (const s of log.sessions) {
      const key = sessionKey(log.log_id, s.session_id);
      const craft = s.headers?.craft_name ? ` · ${s.headers.craft_name}` : "";

      const row = document.createElement("label");
      row.className = "picker-session";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selected.has(key);
      cb.addEventListener("change", () => toggleSession(cb, log, s.session_id));
      row.appendChild(cb);

      const chip = document.createElement("span");
      chip.className = "color-chip";
      if (selected.has(key)) chip.style.background = selected.get(key).color;
      row.appendChild(chip);

      const name = document.createElement("span");
      name.className = "picker-session-name";
      name.textContent = s.name || `Session ${s.session_id + 1}`;
      row.appendChild(name);

      const meta = document.createElement("span");
      meta.className = "picker-session-meta";
      meta.textContent = `${s.duration_s.toFixed(1)}s${craft}`;
      row.appendChild(meta);

      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "rename-btn";
      rename.title = "Rename session";
      rename.setAttribute("aria-label", "Rename session");
      rename.textContent = "✎";
      rename.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        startRename(row, log, s);
      });
      row.appendChild(rename);

      group.appendChild(row);
    }
    pickerEl.appendChild(group);
  }
}

async function toggleSession(cb, log, sid) {
  const key = sessionKey(log.log_id, sid);
  if (!cb.checked) {
    selected.delete(key);
    saveState();
    refreshUI();
    return;
  }
  const color = freeColor();
  if (!color) {
    cb.checked = false;
    toastFn("Up to 8 sessions can be compared — please deselect one first.");
    return;
  }
  cb.disabled = true;
  try {
    const data = await getStepResponse(log.log_id, sid);
    selected.set(key, {
      label: makeLabel(log, sid), color, data,
      headers: sessionHeaders(log, sid),
    });
    saveState();
    refreshUI();
  } catch (e) {
    cb.checked = false;
    cb.disabled = false;
    toastFn(e.message);
  }
}

// Core rename: persist to the server and update every place the label appears
// (picker, charts, tables, and the Gyro-tab dropdown via "session-renamed").
// Shared by the picker's inline editor and the Gyro-tab rename button. Throws
// on network/server error so callers can surface it.
export async function renameSessionByKey(logId, sessionId, rawName) {
  const name = rawName.trim();
  await renameSession(logId, sessionId, name);
  const log = uploadedLogs.find((l) => l.log_id === logId);
  const session = log?.sessions.find((x) => x.session_id === sessionId);
  if (session) { if (name) session.name = name; else delete session.name; }
  const key = sessionKey(logId, sessionId);
  if (log && selected.has(key)) selected.get(key).label = makeLabel(log, sessionId);
  document.dispatchEvent(new CustomEvent("session-renamed"));
  refreshUI();
  return name;
}

// Inline-rename from the picker: swap the row's label for a text input.
function startRename(row, log, s) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "rename-input";
  input.value = s.name || "";
  input.placeholder = defaultSessionLabel(log, s.session_id);
  // clicks inside the row's <label> would otherwise toggle the checkbox
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("mousedown", (e) => e.stopPropagation());

  row.querySelector(".picker-session-name").replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim() !== (s.name || "")) {
      try {
        await renameSessionByKey(log.log_id, s.session_id, input.value);
        return; // renameSessionByKey already refreshed the UI
      } catch (e) {
        toastFn(e.message);
      }
    }
    refreshUI();
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(true); }
    else if (e.key === "Escape") { e.preventDefault(); commit(false); }
  });
  input.addEventListener("blur", () => commit(true));
}

// Resample a curve onto a common 1ms grid so sessions with different log
// rates can share one x-axis per chart.
const GRID_MS = Array.from({ length: 501 }, (_, i) => i);

function resample(timeMs, values) {
  const out = new Array(GRID_MS.length).fill(null);
  let j = 0;
  for (let i = 0; i < GRID_MS.length; i++) {
    const t = GRID_MS[i];
    while (j < timeMs.length - 2 && timeMs[j + 1] < t) j++;
    if (t < timeMs[0] || t > timeMs[timeMs.length - 1]) continue;
    const t0 = timeMs[j], t1 = timeMs[j + 1];
    const frac = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
    out[i] = values[j] + (values[j + 1] - values[j]) * frac;
  }
  return out;
}

// dashed guide line at y=1.0 (target steady state)
function guideLinePlugin() {
  return {
    hooks: {
      draw(u) {
        const y = u.valToPos(1.0, "y", true);
        const ctx = u.ctx;
        ctx.save();
        ctx.strokeStyle = "#8b96a5";
        ctx.setLineDash([6, 6]);
        ctx.beginPath();
        ctx.moveTo(u.bbox.left, y);
        ctx.lineTo(u.bbox.left + u.bbox.width, y);
        ctx.stroke();
        ctx.restore();
      },
    },
  };
}

// Horizontal bar chart: latency per session, one box per axis. Plain HTML -
// with <=8 values this reads faster than any plotted form.
function latencyBox(axis) {
  const box = document.createElement("div");
  box.className = "chart-box";
  box.innerHTML = `<h3>Latency ${axis}</h3>`;
  makeMaximizable(box);

  const entries = [...selected.values()].map(({ label, color, data }) => {
    const d = data.axes[axis];
    return { label, color, value: d && !d.error ? d.metrics.delay_ms : null };
  });
  const max = Math.max(...entries.map((e) => e.value ?? 0), 1) * 1.15;

  for (const e of entries) {
    const row = document.createElement("div");
    row.className = "latency-row";
    const width = e.value == null ? 0 : (e.value / max) * 100;
    row.innerHTML =
      `<span class="latency-label" title="${esc(e.label)}">${esc(e.label)}</span>` +
      `<span class="latency-track"><span class="latency-bar" ` +
      `style="width:${width}%;background:${e.color}"></span></span>` +
      `<span class="latency-value">${e.value == null ? "–" : e.value.toFixed(1) + " ms"}</span>`;
    box.appendChild(row);
  }
  return box;
}

// The three metrics that define "good" step-response performance, all
// lower-is-better, so they share one normalization direction.
const SCORED_METRICS = ["delay_ms", "rise_time_ms", "overshoot_pct"];

// Rank the compared sessions and return the key of the single best one — the
// configuration with the best all-round step response (low overshoot, low
// latency, low rise time). Returns null when a winner isn't meaningful.
//
// A session whose step response couldn't be measured (all metrics missing) is
// ignored rather than treated as a loser — it must not suppress the ranking of
// the sessions that DO have data. Among the remaining "rankable" sessions we
// score on the (axis, metric) cells they all share, so each is judged on equal
// footing: per cell, min-max normalize across those sessions (best value -> 0,
// worst -> 1) and sum. A cell where every session matches adds zero and can't
// skew the result. Lowest total wins; a dead tie yields no winner.
function bestSessionKey() {
  const entries = [...selected.entries()]; // [key, {label,color,data}]
  if (entries.length < 2) return null;

  const isNum = (v) => typeof v === "number" && isFinite(v);
  const cells = [];
  for (const axis of AXES) {
    for (const metric of SCORED_METRICS) {
      cells.push(entries.map(([, s]) => {
        const d = s.data.axes[axis];
        const v = d && !d.error ? d.metrics[metric] : null;
        return isNum(v) ? v : null;
      }));
    }
  }

  const rankable = entries
    .map((_, i) => (cells.some((c) => c[i] != null) ? i : -1))
    .filter((i) => i >= 0);
  if (rankable.length < 2) return null;

  const scores = rankable.map(() => 0);
  let commonCells = 0;
  for (const c of cells) {
    if (!rankable.every((i) => c[i] != null)) continue; // not shared by all
    commonCells++;
    const vals = rankable.map((i) => c[i]);
    const min = Math.min(...vals), span = Math.max(...vals) - min;
    if (span > 0) rankable.forEach((i, k) => { scores[k] += (c[i] - min) / span; });
  }
  if (!commonCells) return null;

  let best = 0;
  for (let k = 1; k < scores.length; k++) if (scores[k] < scores[best]) best = k;
  if (scores.every((s) => s === scores[best])) return null; // no separation
  return entries[rankable[best]][0];
}

const BEST_TITLE =
  "Best all-round step response: lowest combined latency, rise time and " +
  "overshoot across the compared sessions.";

function metricsTable(axis, bestKey) {
  const table = document.createElement("table");
  table.className = "compare-table";
  table.innerHTML =
    "<thead><tr><th></th><th>Session</th><th>PID</th><th>Latency</th>" +
    "<th>Rise</th><th>Peak</th><th>Overshoot</th></tr></thead>";
  const tbody = document.createElement("tbody");
  for (const [key, { label, color, data }] of selected.entries()) {
    const d = data.axes[axis];
    const heart = key === bestKey
      ? `<span class="best-badge" title="${BEST_TITLE}">❤️</span> `
      : "";
    const tr = document.createElement("tr");
    if (d?.error || !d) {
      tr.innerHTML = `<td><span class="color-chip" style="background:${color}"></span></td>` +
        `<td>${heart}${esc(label)}</td><td colspan="5" class="dim">${esc(d?.error ?? "-")}</td>`;
    } else {
      const m = d.metrics;
      const fmt = (v, u = "") => (v == null ? "–" : `${v}${u}`);
      tr.innerHTML =
        `<td><span class="color-chip" style="background:${color}"></span></td>` +
        `<td>${heart}${esc(label)}</td>` +
        `<td>${d.pid ? esc(d.pid.split(",").slice(0, 3).join("/")) : "–"}</td>` +
        `<td>${fmt(m.delay_ms, " ms")}</td>` +
        `<td>${fmt(m.rise_time_ms, " ms")}</td>` +
        `<td>${fmt(m.peak)}</td>` +
        `<td>${fmt(m.overshoot_pct, " %")}</td>`;
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

// Helpers to pull values out of the raw Betaflight header block.
const _triax = (h, key) => {
  const p = String(h[key] ?? "").split(",").map((s) => s.trim());
  return p[0] ? p.join("/") : "";
};
const _pid = (h, axis) => {
  const p = String(h[`${axis}PID`] ?? "").split(",").map((s) => s.trim());
  return p[0] ? `${p[0]}/${p[1] ?? ""}/${p[2] ?? ""}` : "";
};

// Config comparison spec: parameters (rows), grouped. Rendered as a matrix with
// one column per selected session so tuning changes across sessions are visible
// at a glance; rows that differ between sessions are highlighted.
const CFG_FIELDS = [
  { group: "General" },
  { label: "Firmware", get: (h) => h["Firmware revision"] },
  { label: "Looptime", get: (h) => (h["looptime"] ? `${h["looptime"]} µs` : "") },
  { label: "PID denom", get: (h) => h["pid_process_denom"] },
  { group: "PID / Gains" },
  { label: "PID Roll", get: (h) => _pid(h, "roll") },
  { label: "PID Pitch", get: (h) => _pid(h, "pitch") },
  { label: "PID Yaw", get: (h) => _pid(h, "yaw") },
  { label: "D-Min", get: (h) => _triax(h, "d_min") },
  { label: "FF-Weight", get: (h) => _triax(h, "ff_weight") },
  { group: "Rates" },
  { label: "RC-Rate", get: (h) => _triax(h, "rc_rates") },
  { label: "Super-Rate", get: (h) => _triax(h, "rates") },
  { label: "Expo", get: (h) => _triax(h, "rc_expo") },
  { label: "Rates type", get: (h) => h["rates_type"] },
  { group: "Simplified Tuning" },
  { label: "Master mult.", get: (h) => h["simplified_master_multiplier"] },
  { label: "PI-Gain", get: (h) => h["simplified_pi_gain"] },
  { label: "I-Gain", get: (h) => h["simplified_i_gain"] },
  { label: "D-Gain", get: (h) => h["simplified_d_gain"] },
  { label: "D-Max-Gain", get: (h) => h["simplified_dmax_gain"] },
  { label: "FF-Gain", get: (h) => h["simplified_feedforward_gain"] },
  { label: "Mode", get: (h) => h["simplified_pids_mode"] },
  { group: "Feedforward" },
  { label: "FF-Transition", get: (h) => h["feedforward_transition"] },
  { label: "FF-Boost", get: (h) => h["feedforward_boost"] },
];

// Flatten CFG_FIELDS against the selected sessions into render-ready rows,
// tagging each field with whether it's present anywhere and whether it differs.
function configRows() {
  const sessions = [...selected.values()];
  const rows = CFG_FIELDS.map((field) => {
    if (field.group) return { group: field.group };
    const vals = sessions.map((s) => {
      const v = field.get(s.headers || {});
      return v == null ? "" : String(v);
    });
    return {
      label: field.label,
      vals,
      present: !vals.every((v) => v === ""),
      differs: new Set(vals).size > 1,
    };
  });
  return { sessions, rows };
}

// Config comparison box. Shows only differing rows by default; a header toggle
// expands to the full matrix. With a single session "differences" is moot, so
// everything is shown and the toggle is hidden.
function buildConfigBox() {
  const { sessions, rows } = configRows();
  const multi = sessions.length >= 2;
  const showAll = cfgShowAll || !multi;
  const sameCount = rows.filter((r) => r.label && r.present && !r.differs).length;

  const box = document.createElement("div");
  box.className = "chart-box";

  const head = document.createElement("div");
  head.className = "chart-head";
  head.innerHTML = "<h3>Configuration</h3>";
  if (multi) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "cfg-toggle";
    toggle.textContent = showAll
      ? "Differences only"
      : `Show all (${sameCount} identical)`;
    toggle.addEventListener("click", () => {
      cfgShowAll = !cfgShowAll;
      localStorage.setItem("pidtuner-cfg-showall", cfgShowAll ? "1" : "0");
      box.replaceWith(buildConfigBox());
    });
    head.appendChild(toggle);
  }
  box.appendChild(head);

  const table = document.createElement("table");
  table.className = "compare-table cfg-table";

  const thead = document.createElement("thead");
  const htr = document.createElement("tr");
  htr.innerHTML = "<th>Parameter</th>";
  for (const s of sessions) {
    const th = document.createElement("th");
    th.innerHTML =
      `<span class="color-chip" style="background:${s.color}"></span>` +
      `<span class="cfg-sess">${esc(s.label)}</span>`;
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  let pendingGroup = null;
  let fieldsShown = 0;
  for (const row of rows) {
    if (row.group) { pendingGroup = row.group; continue; }
    if (!row.present || (!showAll && !row.differs)) continue;

    if (pendingGroup) {
      const tr = document.createElement("tr");
      tr.className = "cfg-group";
      tr.innerHTML = `<th colspan="${sessions.length + 1}">${pendingGroup}</th>`;
      tbody.appendChild(tr);
      pendingGroup = null;
    }

    const tr = document.createElement("tr");
    if (row.differs) tr.className = "cfg-diff";
    const th = document.createElement("th");
    th.textContent = row.label;
    tr.appendChild(th);
    for (const v of row.vals) {
      const td = document.createElement("td");
      td.textContent = v === "" ? "–" : v;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
    fieldsShown++;
  }
  table.appendChild(tbody);

  if (fieldsShown === 0) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = multi
      ? "No differences in the configuration."
      : "No configuration data in the log.";
    box.appendChild(note);
  } else {
    box.appendChild(table);
  }
  return box;
}

function renderCharts(container) {
  closeMaximized();
  charts.forEach((c) => c.destroy());
  charts = [];
  container.innerHTML = "";
  if (selected.size === 0) return;

  container.appendChild(buildConfigBox());

  const bestKey = bestSessionKey();
  const group = makeSyncGroup();

  // One row per axis: the step-response curve on the left, its latency bars
  // directly to the right (they stack on narrow/mobile viewports via CSS).
  for (const axis of AXES) {
    const row = document.createElement("div");
    row.className = "sr-pair";
    container.appendChild(row);

    const entries = [...selected.values()].filter(
      (s) => s.data.axes[axis] && !s.data.axes[axis].error
    );

    const series = [
      { label: "t (ms)", value: (u, v) => (v == null ? "-" : v.toFixed(1)) },
    ];
    const chartData = [GRID_MS];
    for (const { label, color, data } of entries) {
      const d = data.axes[axis];
      series.push({ label, stroke: color, width: 2 });
      chartData.push(resample(d.time_ms, d.consensus));
    }

    const chart = createChart(row, {
      title: `Step Response ${axis}`,
      series,
      height: 280,
      group,
      xLabel: "time (ms)",
      extraPlugins: [guideLinePlugin()],
    });
    chart.u.setData(chartData);
    chart.u.setScale("y", { min: -0.2, max: 2.0 });
    chart.box.classList.add("sr-curve");
    chart.box.appendChild(metricsTable(axis, bestKey));
    charts.push(chart);

    const lat = latencyBox(axis);
    lat.classList.add("sr-latency");
    row.appendChild(lat);
  }
}
