// Noise-spectrum tab: per-axis amplitude spectrum (Welch) of filtered/unfiltered
// gyro, with user-placed frequency markers.
//
// Markers are the point of the tool: add one ("+ Marker"), drag it onto a peak
// to read its frequency, double-click to give it a label, × to remove it (or
// "Clear" for all). Each axis chart owns its own independent set of markers —
// they are NOT synchronized across axes — so you can annotate roll, pitch and
// yaw separately (e.g. a motor peak that only shows on one axis). Every marker
// gets its own color.
//
// Reading peak frequencies this way is how you locate noise sources and set the
// Betaflight gyro lowpass / dynamic notch / RPM filters. Gyro motion below
// ~30 Hz dwarfs the noise, so each chart auto-frames its Y axis to the noise
// band above that (Shift+scroll out to see the low-frequency content).

import { createChart } from "./uplotSetup.js";
import { makeSyncGroup } from "./zoomPlugin.js";

const AXES = ["roll", "pitch", "yaw"];

// Same colors/keys as the gyro tab so "raw" vs "filtered" reads identically.
const SIGNALS = [
  { key: "unfiltered", label: "Gyro (raw)",      color: "#d95926", def: true },
  { key: "filtered",   label: "Gyro (filtered)", color: "#3987e5", def: true },
];

// Validated categorical palette (dark mode) — one distinct color per marker,
// cycling once exhausted.
const MARKER_PALETTE = ["#4da3ff", "#3ecf8e", "#ff9f43", "#d55181",
                        "#9085e9", "#e66767", "#c98500", "#199e70"];

const NOISE_FLOOR_HZ = 30;   // below this is craft motion, not noise
const RIGHT_FLIP_FRAC = 0.82; // flip a marker's label to the left near the right edge

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

let charts = [];        // [{ u, box, destroy, sig, ctrl }]
let controlsEl = null;

// ---- per-chart marker controller ----------------------------------------

// Owns one chart's markers and their DOM overlay inside the plot's interaction
// area. Positions are derived from the (linear) x scale directly, so markers
// track independent zoom/pan on each chart.
function createMarkerController(u, nyquist) {
  const over = u.over;
  const overlay = document.createElement("div");
  overlay.className = "spec-marker-layer";
  over.appendChild(overlay);

  const markers = [];
  let colorIdx = 0;

  const xToPx = (freq) => {
    const { min, max } = u.scales.x;
    return ((freq - min) / (max - min)) * over.clientWidth;
  };

  function positionOne(m) {
    const { min, max } = u.scales.x;
    const inView = m.freq >= min && m.freq <= max;
    m.line.style.display = inView ? "" : "none";
    if (!inView) return;
    const x = xToPx(m.freq);
    m.line.style.left = x + "px";
    m.pill.classList.toggle("flip", x > over.clientWidth * RIGHT_FLIP_FRAC);
  }

  function layout() {
    for (const m of markers) positionOne(m);
  }

  function refreshText(m) {
    const hz = Math.round(m.freq) + " Hz";
    m.text.textContent = m.label ? `${m.label} · ${hz}` : hz;
  }

  function startRename(m) {
    if (m.editing) return;
    m.editing = true;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "rename-input spec-rename";
    input.value = m.label;
    input.placeholder = "label";
    // keep the input's own mouse events from starting a drag / zoom reset
    ["mousedown", "dblclick", "click"].forEach((ev) =>
      input.addEventListener(ev, (e) => e.stopPropagation()));
    m.text.replaceWith(input);
    input.focus();
    input.select();

    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      if (save) m.label = input.value.trim();
      m.editing = false;
      input.replaceWith(m.text);
      refreshText(m);
      positionOne(m);
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  }

  function removeMarker(m) {
    const i = markers.indexOf(m);
    if (i >= 0) markers.splice(i, 1);
    m.line.remove();
  }

  function beginDrag(m, e) {
    e.preventDefault();
    e.stopPropagation();   // don't let the zoom plugin start a pan
    const rect = over.getBoundingClientRect();
    const onMove = (ev) => {
      const w = over.clientWidth;
      const x = clamp(ev.clientX - rect.left, 0, w);
      const { min, max } = u.scales.x;
      m.freq = clamp(min + (x / w) * (max - min), 0, nyquist);
      m.line.style.left = x + "px";
      m.pill.classList.toggle("flip", x > w * RIGHT_FLIP_FRAC);
      refreshText(m);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // Touch equivalent of beginDrag. `fromPill` markers also treat a stationary
  // double-tap as "rename" (touch has no dblclick). stopPropagation keeps the
  // chart's own touch pan/zoom from firing.
  function beginTouchDrag(m, e, fromPill) {
    if (e.touches.length !== 1) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = over.getBoundingClientRect();
    const startX = e.touches[0].clientX, startY = e.touches[0].clientY;
    let moved = false;
    const onMove = (ev) => {
      if (ev.touches.length !== 1) return;
      ev.preventDefault();
      const t = ev.touches[0];
      if (Math.abs(t.clientX - startX) > 6 || Math.abs(t.clientY - startY) > 6) moved = true;
      const w = over.clientWidth;
      const x = clamp(t.clientX - rect.left, 0, w);
      const { min, max } = u.scales.x;
      m.freq = clamp(min + (x / w) * (max - min), 0, nyquist);
      m.line.style.left = x + "px";
      m.pill.classList.toggle("flip", x > w * RIGHT_FLIP_FRAC);
      refreshText(m);
    };
    const onEnd = () => {
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onEnd);
      if (fromPill && !moved) {
        const now = Date.now();
        if (now - m._lastTap < 300) { m._lastTap = 0; startRename(m); }
        else m._lastTap = now;
      }
    };
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onEnd);
  }

  function addMarker(freq, label = "") {
    const color = MARKER_PALETTE[colorIdx % MARKER_PALETTE.length];
    colorIdx++;
    const m = { freq: clamp(freq, 0, nyquist), label, color, editing: false, _lastTap: 0 };

    const line = document.createElement("div");
    line.className = "spec-marker-line";
    line.style.borderLeftColor = color;

    // Wide transparent strip centered on the 1px line so the whole line is a
    // drag handle, not just the pill. Sits behind the pill (added first).
    const hit = document.createElement("div");
    hit.className = "spec-marker-hit";

    const pill = document.createElement("div");
    pill.className = "spec-marker-pill";
    pill.style.borderColor = color;
    pill.style.color = color;
    pill.title = "Drag to move · double-click to label";

    const text = document.createElement("span");
    text.className = "spec-marker-text";

    const close = document.createElement("button");
    close.type = "button";
    close.className = "spec-marker-close";
    close.textContent = "×";
    close.title = "Remove marker";
    close.setAttribute("aria-label", "Remove marker");

    pill.append(text, close);
    line.append(hit, pill);
    overlay.appendChild(line);

    Object.assign(m, { line, pill, text });
    refreshText(m);

    pill.addEventListener("mousedown", (e) => beginDrag(m, e));
    pill.addEventListener("touchstart", (e) => beginTouchDrag(m, e, true), { passive: false });
    pill.addEventListener("dblclick", (e) => {
      e.preventDefault(); e.stopPropagation(); startRename(m);
    });
    hit.addEventListener("mousedown", (e) => beginDrag(m, e));
    hit.addEventListener("touchstart", (e) => beginTouchDrag(m, e, false), { passive: false });
    close.addEventListener("mousedown", (e) => e.stopPropagation());
    close.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation(); removeMarker(m);
    });

    markers.push(m);
    positionOne(m);
    return m;
  }

  function clearAll() {
    while (markers.length) removeMarker(markers[0]);
  }

  // Strongest peak within the current view and above the noise floor — where a
  // freshly added marker lands, so it drops onto something meaningful.
  function suggestFreq() {
    const freq = u.data[0];
    // u.scales.x isn't populated synchronously right after setData; fall back
    // to the full data extent so a marker added on first render still lands on
    // the real peak (rather than 0 Hz).
    let { min, max } = u.scales.x;
    if (!(max > min)) { min = freq[0]; max = freq[freq.length - 1]; }
    const lo = Math.max(min, NOISE_FLOOR_HZ);
    let bestF = (min + max) / 2, bestA = -Infinity;
    for (let s = 1; s < u.series.length; s++) {
      if (!u.series[s].show) continue;
      const arr = u.data[s];
      if (!arr) continue;
      for (let j = 0; j < freq.length; j++) {
        const f = freq[j];
        if (f < lo || f > max) continue;
        if (arr[j] > bestA) { bestA = arr[j]; bestF = f; }
      }
    }
    return bestF;
  }

  return { layout, addMarker, clearAll, suggestFreq };
}

// Keeps a chart's markers aligned on every scale change / redraw. The
// controller is created right after construction (see renderSpectrum) and
// dropped into `holder`, so this does not depend on uPlot's ready-hook timing.
function relayoutPlugin(holder) {
  return {
    hooks: {
      setScale() { holder.ctrl && holder.ctrl.layout(); },
      draw() { holder.ctrl && holder.ctrl.layout(); },
    },
  };
}

function addToolbar(chart, ctrl) {
  const head = chart.box.querySelector(".chart-head");
  if (!head) return;
  const bar = document.createElement("div");
  bar.className = "spec-toolbar";

  const add = document.createElement("button");
  add.type = "button";
  add.className = "spec-btn";
  add.textContent = "+ Marker";
  add.title = "Add a frequency marker (drag to position, double-click to label)";
  add.addEventListener("click", () => ctrl.addMarker(ctrl.suggestFreq()));

  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "spec-btn";
  clear.textContent = "Clear";
  clear.title = "Remove all markers on this chart";
  clear.addEventListener("click", () => ctrl.clearAll());

  bar.append(add, clear);
  head.insertBefore(bar, head.querySelector(".max-btn"));
}

// ---- Y framing ----------------------------------------------------------

// Peak amplitude above the noise floor; frames Y so the noise band fills the
// chart (sub-30 Hz motion then runs off the top).
function noiseBandMax(freq, arrays) {
  let hi = 0;
  for (const arr of arrays) {
    if (!arr) continue;
    for (let j = 0; j < freq.length; j++) {
      if (freq[j] < NOISE_FLOOR_HZ) continue;
      if (arr[j] > hi) hi = arr[j];
    }
  }
  return hi;
}

// ---- render -------------------------------------------------------------

function fmtAmp(u, v) {
  return v == null ? "-" : v.toFixed(2) + " °/s";
}

export function renderSpectrum(container, data) {
  destroySpectrum();
  const group = makeSyncGroup();
  const freq = data.freq;

  const present = new Set();
  for (const axis of AXES)
    for (const s of SIGNALS)
      if (data.axes[axis] && data.axes[axis][s.key]) present.add(s.key);

  for (const axis of AXES) {
    const e = data.axes[axis];
    if (!e) continue;

    const series = [
      { label: "f (Hz)", value: (u, v) => (v == null ? "-" : v.toFixed(1)) },
    ];
    const chartData = [freq];
    const sig = new Map();
    for (const s of SIGNALS) {
      const arr = e[s.key];
      if (!arr) continue;
      sig.set(s.key, series.length);
      series.push({ label: s.label, stroke: s.color, width: 1, show: s.def, value: fmtAmp });
      chartData.push(arr);
    }

    const bandMax = noiseBandMax(freq, [e.unfiltered, e.filtered]);
    const yTop = bandMax > 0 ? bandMax * 1.15 : 1;

    const holder = {};
    const chart = createChart(container, {
      title: `Gyro ${axis} — noise spectrum`,
      series, height: 250, group, xLabel: "frequency (Hz)",
      yRange: () => [0, yTop],
      extraPlugins: [relayoutPlugin(holder)],
    });
    chart.u.setData(chartData);
    chart.sig = sig;

    const ctrl = createMarkerController(chart.u, data.nyquist);
    holder.ctrl = ctrl;
    chart.ctrl = ctrl;
    addToolbar(chart, ctrl);
    // start each chart with one marker on its own strongest peak, so the tool
    // is immediately useful and the interaction is discoverable
    ctrl.addMarker(ctrl.suggestFreq());
    charts.push(chart);
  }

  buildControls(present);
}

// ---- signal toggles -----------------------------------------------------

function buildControls(present) {
  controlsEl = document.getElementById("spectrum-controls");
  if (!controlsEl) return;
  controlsEl.innerHTML = "";
  for (const s of SIGNALS) {
    if (!present.has(s.key)) continue;
    const label = document.createElement("label");
    label.className = "signal-toggle";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = s.def;
    cb.addEventListener("change", () => setSignal(s.key, cb.checked));

    const chip = document.createElement("span");
    chip.className = "sig-chip";
    chip.style.background = s.color;

    label.append(cb, chip, document.createTextNode(s.label));
    controlsEl.appendChild(label);
  }
}

function setSignal(key, show) {
  for (const c of charts) {
    const idx = c.sig?.get(key);
    if (idx != null) c.u.setSeries(idx, { show });
  }
}

export function destroySpectrum() {
  charts.forEach((c) => c.destroy());
  charts = [];
  if (controlsEl) controlsEl.innerHTML = "";
}
