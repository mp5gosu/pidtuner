// Roll/pitch/yaw gyro charts with per-signal toggles.
//
// Each axis chart carries every signal the log provides (filtered/unfiltered
// gyro, setpoint, tracking error, P/I/D terms). A single row of checkboxes
// above the charts shows/hides a signal across all three axes at once.

import { createChart } from "./uplotSetup.js";
import { makeSyncGroup } from "./zoomPlugin.js";

const AXES = ["roll", "pitch", "yaw"];

// Signal catalogue. Order here == uPlot series-index order per chart, so the
// index map below stays stable. Colors come from the validated dark-surface
// categorical palette; identity is carried by the legend + the checkbox chip,
// never color alone. Gyro filtered/unfiltered are on by default (the classic
// filter-effectiveness view); everything else starts hidden.
const SIGNALS = [
  { key: "unfiltered", label: "Gyro (raw)",      color: "#d95926", unit: "°/s", def: true },
  { key: "filtered",   label: "Gyro (filtered)", color: "#3987e5", unit: "°/s", def: true },
  { key: "setpoint",   label: "Setpoint",       color: "#199e70", unit: "°/s", def: false },
  { key: "error",      label: "PID-Error",      color: "#e66767", unit: "°/s", def: false },
  { key: "p",          label: "P-Term",         color: "#9085e9", unit: "",    def: false },
  { key: "i",          label: "I-Term",         color: "#c98500", unit: "",    def: false },
  { key: "d",          label: "D-Term",         color: "#d55181", unit: "",    def: false },
];

let charts = [];        // [{ u, box, destroy, sig: Map(key -> seriesIdx) }]
let controlsEl = null;

function fmtVal(unit) {
  return (u, v) => (v == null ? "-" : v.toFixed(1) + (unit ? " " + unit : ""));
}

export function renderGyro(container, data) {
  destroyGyro();
  const group = makeSyncGroup();

  // union of signals actually present across all axes -> which checkboxes to show
  const present = new Set();
  for (const axis of AXES)
    for (const s of SIGNALS)
      if (data.series[`${axis}.${s.key}`]) present.add(s.key);

  for (const axis of AXES) {
    const time = data.series[`${axis}.time_s`];
    if (!time) continue;

    const series = [
      { label: "t (s)", value: (u, v) => (v == null ? "-" : v.toFixed(4)) },
    ];
    const chartData = [time];
    const sig = new Map();
    for (const s of SIGNALS) {
      const arr = data.series[`${axis}.${s.key}`];
      if (!arr) continue;
      sig.set(s.key, series.length);
      series.push({
        label: s.label, stroke: s.color, width: 1,
        show: s.def, value: fmtVal(s.unit),
      });
      chartData.push(arr);
    }

    const chart = createChart(container, {
      title: `Gyro ${axis}`, series, height: 240, group, xLabel: "time (s)",
    });
    chart.u.setData(chartData);
    chart.sig = sig;
    charts.push(chart);
  }

  buildControls(present);
}

function buildControls(present) {
  controlsEl = document.getElementById("gyro-controls");
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

export function destroyGyro() {
  charts.forEach((c) => c.destroy());
  charts = [];
  if (controlsEl) controlsEl.innerHTML = "";
}
