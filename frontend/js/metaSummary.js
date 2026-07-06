// Compact metadata summary bar under the header — an at-a-glance recap of the
// active session (firmware, sample rate, sizes, …) that stays visible across
// all tabs. The full Rates table lives on the Gyro tab (see sessionInfo.js).

const barEl = () => document.getElementById("meta-summary");

function fmtBytes(b) {
  if (b == null || !isFinite(b)) return null;
  const units = ["B", "KB", "MB", "GB"];
  let v = b, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function clearMetaSummary() {
  const box = barEl();
  if (box) box.innerHTML = "";
}

// `file` is the uploaded File's {name, size} captured at upload time (optional).
export function renderMetaSummary(session, file) {
  const box = barEl();
  if (!box) return;
  if (!session) { box.innerHTML = ""; return; }

  const h = session.headers || {};
  const raw = session.headers_raw || {};
  const items = [];
  const add = (label, value) => {
    if (value == null || value === "") return;
    items.push(`<span class="meta-item"><span class="meta-label">${label}</span>` +
      `<span class="meta-value">${value}</span></span>`);
  };

  add("Craft", h.craft_name);
  add("Firmware", h.fw_revision || h.fw_type);
  if (session.sample_rate_hz) {
    const hz = Math.round(session.sample_rate_hz);
    add("Sample rate", `${hz} Hz · Nyq ${Math.round(hz / 2)} Hz`);
  }
  add("Rates", raw.rates_type);
  if (typeof session.duration_s === "number") add("Duration", `${session.duration_s.toFixed(1)} s`);
  const logSize = fmtBytes(file?.size);
  const sessSize = fmtBytes(session.size_bytes);
  if (logSize && sessSize && logSize !== sessSize) add("Size", `${logSize} · session ${sessSize}`);
  else add("Size", logSize || sessSize);

  box.innerHTML = items.join("");
}
