// Shared uPlot option factory + responsive sizing via ResizeObserver.

import { zoomPlugin } from "./zoomPlugin.js";

const AXIS_STYLE = {
  stroke: "#8b96a5",
  grid: { stroke: "#2e3745", width: 1 },
  ticks: { stroke: "#2e3745" },
};

// ---- maximize/restore ---------------------------------------------------

let backdrop = null;
let restoreFn = null; // restores the currently maximized box, if any

function ensureBackdrop() {
  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.className = "chart-backdrop hidden";
    backdrop.addEventListener("click", () => restoreFn?.());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") restoreFn?.();
    });
    document.body.appendChild(backdrop);
  }
  return backdrop;
}

// Restore whatever chart is currently maximized (call before tearing down
// chart boxes, so no orphaned backdrop survives a re-render).
export function closeMaximized() {
  restoreFn?.();
}

// Adds a header row with a maximize button to a .chart-box. Works for any
// box (uPlot charts, latency bars); resizing of uPlot itself happens via
// the box's ResizeObserver reacting to the CSS class change.
export function makeMaximizable(box) {
  const head = document.createElement("div");
  head.className = "chart-head";
  const h3 = box.querySelector("h3");
  if (h3) head.appendChild(h3);

  const btn = document.createElement("button");
  btn.className = "max-btn";
  btn.title = "Maximize (Esc closes)";
  btn.textContent = "⛶";
  head.appendChild(btn);
  box.prepend(head);

  function restore() {
    box.classList.remove("maximized");
    backdrop?.classList.add("hidden");
    btn.textContent = "⛶";
    btn.title = "Maximize (Esc closes)";
    restoreFn = null;
  }

  function maximize() {
    restoreFn?.(); // only one maximized chart at a time
    box.classList.add("maximized");
    ensureBackdrop().classList.remove("hidden");
    btn.textContent = "✕";
    btn.title = "Restore (Esc)";
    restoreFn = restore;
  }

  btn.addEventListener("click", () =>
    box.classList.contains("maximized") ? restore() : maximize()
  );
}

// ---- chart factory ------------------------------------------------------

export function createChart(container, { title, series, height = 260, group = null, xLabel, extraPlugins = [] }) {
  const box = document.createElement("div");
  box.className = "chart-box";
  if (title) {
    const h = document.createElement("h3");
    h.textContent = title;
    box.appendChild(h);
  }
  makeMaximizable(box);

  const el = document.createElement("div");
  el.className = "chart-el";
  box.appendChild(el);
  container.appendChild(box);

  const opts = {
    width: el.clientWidth || 800,
    height,
    // pan/zoom handled by zoomPlugin; disable built-in drag-select
    cursor: {
      drag: { x: false, y: false },
      points: { size: 6 },
    },
    scales: {
      x: { time: false },
      y: {},
    },
    axes: [
      { ...AXIS_STYLE, label: xLabel, labelSize: 14 },
      { ...AXIS_STYLE, size: 60 },
    ],
    series,
    plugins: [zoomPlugin({ group }), ...extraPlugins],
    legend: { live: true },
  };

  const u = new uPlot(opts, [[]], el);
  (window.__uplots ??= new Set()).add(u); // debug/test access to instances

  const ro = new ResizeObserver(() => {
    const w = el.clientWidth;
    if (w <= 0) return;
    let h = height;
    if (box.classList.contains("maximized")) {
      // .maximized is a flex column; chart-el flexes to the free space
      const legendH = el.querySelector(".u-legend")?.offsetHeight ?? 0;
      h = Math.max(200, el.clientHeight - legendH);
    }
    if (w !== u.width || h !== u.height) u.setSize({ width: w, height: h });
  });
  ro.observe(el);

  return {
    u,
    box,
    destroy: () => {
      if (box.classList.contains("maximized")) closeMaximized();
      ro.disconnect();
      window.__uplots?.delete(u);
      u.destroy();
      box.remove();
    },
  };
}
