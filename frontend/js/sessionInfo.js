// Rates panel for the active session (Gyro tab).
//
// The rate config (RC-Rate / Super-Rate / Expo) shapes the setpoint & stick
// response you see in the gyro traces, so it stays on this tab. The full
// PID/tuning configuration is compared across sessions on the Step-Response
// tab (see configTable in compare.js). Reads session.headers_raw.

const AXES = [
  { key: "roll", label: "Roll" },
  { key: "pitch", label: "Pitch" },
  { key: "yaw", label: "Yaw" },
];

const panelEl = () => document.getElementById("session-info");
const parts = (raw) => (raw ? String(raw).split(",").map((s) => s.trim()) : []);
const cell = (v) => (v == null || v === "" ? "–" : v);

export function clearSessionInfo() {
  const box = panelEl();
  if (box) box.innerHTML = "";
}

export function renderSessionInfo(session) {
  const box = panelEl();
  if (!box) return;
  const h = session?.headers_raw;
  if (!h || Object.keys(h).length === 0) {
    box.innerHTML = "";
    return;
  }

  const rc = parts(h["rc_rates"]);
  const sr = parts(h["rates"]);
  const ex = parts(h["rc_expo"]);
  const rows = AXES.map(
    ({ label }, i) =>
      `<tr><th>${label}</th><td>${cell(rc[i])}</td><td>${cell(sr[i])}</td><td>${cell(ex[i])}</td></tr>`
  ).join("");

  box.innerHTML =
    `<div class="chart-head"><h3>Rates${h["rates_type"] ? ` (type ${h["rates_type"]})` : ""}</h3></div>` +
    `<table class="si-table"><thead><tr><th></th><th>RC-Rate</th><th>Super</th><th>Expo</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`;
}
