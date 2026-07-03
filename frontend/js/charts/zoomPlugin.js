// Wheel-zoom + drag-pan + box-zoom + double-click-reset plugin for uPlot.
//
// Interactions (modifier-driven, scope-/analyzer-style):
//   wheel            -> zoom time axis (X), anchored at cursor
//   shift + wheel    -> zoom amplitude axis (Y), anchored at cursor
//   ctrl/cmd + wheel -> zoom both axes at cursor (also fires on trackpad pinch)
//   ctrl+shift + whl -> zoom only the horizontal (time) axis, at cursor
//   alt + wheel      -> pan horizontally (scroll through time)
//   left-drag        -> pan X and Y
//   alt + left-drag  -> draw a box, zoom to that rectangle
//   double-click     -> reset to full data range
//
// `group`: charts sharing the same group object mirror their X scale
// (time axis) so panning/zooming one chart moves its siblings.

const ZOOM_FACTOR = 0.8;
const PAN_FRACTION = 0.15; // fraction of the visible span moved per wheel notch
const BOX_MIN_PX = 5; // ignore accidental tiny drags

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// Global toggle: mirror X-scale changes to sibling charts of the same group.
// Off by default; wired to the "Link axes" checkbox in the header.
let syncEnabled = false;

export function setSyncEnabled(v) {
  syncEnabled = v;
}

export function makeSyncGroup() {
  return { charts: [], broadcasting: false };
}

export function zoomPlugin({ group = null } = {}) {
  // x-data extent, refreshed whenever data is (re)set; used to clamp pan/zoom
  // and as the reset target for the time axis. Snapshotting at ready() alone is
  // not enough: the chart is constructed with empty data and filled via setData.
  let xFull = { min: 0, max: 1 };

  function xExtent(u) {
    const xs = u.data && u.data[0];
    if (xs && xs.length) return { min: xs[0], max: xs[xs.length - 1] };
    return { min: u.scales.x.min ?? 0, max: u.scales.x.max ?? 1 };
  }

  // Vertical extent over the currently-visible series only, so a reset refits Y
  // to what is actually drawn (relevant once signals can be toggled on/off).
  function visibleYExtent(u) {
    let min = Infinity, max = -Infinity;
    for (let i = 1; i < u.series.length; i++) {
      if (!u.series[i].show) continue;
      const arr = u.data[i];
      if (!arr) continue;
      for (let j = 0; j < arr.length; j++) {
        const v = arr[j];
        if (v == null || Number.isNaN(v)) continue;
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    if (min > max) return { min: u.scales.y.min ?? 0, max: u.scales.y.max ?? 1 };
    const pad = (max - min) * 0.05 || 1;
    return { min: min - pad, max: max + pad };
  }

  return {
    hooks: {
      setData(u) { xFull = xExtent(u); },
      ready(u) {
        const over = u.over;
        xFull = xExtent(u);

        if (group) group.charts.push(u);

        // rubber-band box overlay for alt-drag zoom
        const zoomBox = document.createElement("div");
        zoomBox.style.cssText =
          "position:absolute;display:none;pointer-events:none;z-index:10;" +
          "background:rgba(77,163,255,0.15);border:1px solid #4da3ff;";
        over.appendChild(zoomBox);

        // "grab" signals the plot is pannable; swapped to "grabbing"/"crosshair"
        // for the duration of a drag, then restored on mouseup.
        over.style.cursor = "grab";

        function setX(min, max) {
          // clamp to full range so you can't zoom/pan into nothingness
          const span = max - min;
          if (span >= xFull.max - xFull.min) { min = xFull.min; max = xFull.max; }
          else if (min < xFull.min) { min = xFull.min; max = min + span; }
          else if (max > xFull.max) { max = xFull.max; min = max - span; }
          u.setScale("x", { min, max });
          if (group && syncEnabled && !group.broadcasting) {
            group.broadcasting = true;
            for (const other of group.charts) {
              if (other !== u) other.setScale("x", { min, max });
            }
            group.broadcasting = false;
          }
        }

        // Zoom Y about a cursor-anchored value.
        function zoomY(factor, pivot) {
          const { min, max } = u.scales.y;
          u.setScale("y", { min: pivot - (pivot - min) * factor, max: pivot + (max - pivot) * factor });
        }
        // Zoom X about a cursor-anchored value (clamped + group-synced via setX).
        function zoomX(factor, pivot) {
          const { min, max } = u.scales.x;
          setX(pivot - (pivot - min) * factor, pivot + (max - pivot) * factor);
        }

        over.addEventListener("wheel", (e) => {
          e.preventDefault();
          const rect = over.getBoundingClientRect();

          // Alt + wheel -> horizontal pan (scroll through time), no zoom.
          // Down/right advances toward later time; clamped to the data range.
          if (e.altKey) {
            const { min, max } = u.scales.x;
            const raw = e.deltaY || e.deltaX;
            const shift = (raw > 0 ? 1 : -1) * (max - min) * PAN_FRACTION;
            setX(min + shift, max + shift);
            return;
          }

          const factor = e.deltaY < 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR;

          if ((e.ctrlKey || e.metaKey) && e.shiftKey) {
            // ctrl+shift -> constrain the zoom to the horizontal (time) axis
            zoomX(factor, u.posToVal(e.clientX - rect.left, "x"));
          } else if (e.ctrlKey || e.metaKey) {
            // zoom both axes together (also fires on trackpad pinch-to-zoom)
            zoomX(factor, u.posToVal(e.clientX - rect.left, "x"));
            zoomY(factor, u.posToVal(e.clientY - rect.top, "y"));
          } else if (e.shiftKey) {
            zoomY(factor, u.posToVal(e.clientY - rect.top, "y"));
          } else {
            zoomX(factor, u.posToVal(e.clientX - rect.left, "x"));
          }
        }, { passive: false });

        over.addEventListener("mousedown", (e) => {
          if (e.button !== 0) return;
          e.preventDefault();
          const rect = over.getBoundingClientRect();

          if (e.altKey) {
            over.style.cursor = "crosshair";
            const sx = clamp(e.clientX - rect.left, 0, over.clientWidth);
            const sy = clamp(e.clientY - rect.top, 0, over.clientHeight);
            zoomBox.style.display = "block";
            zoomBox.style.left = sx + "px";
            zoomBox.style.top = sy + "px";
            zoomBox.style.width = "0px";
            zoomBox.style.height = "0px";

            function boxMove(ev) {
              const cx = clamp(ev.clientX - rect.left, 0, over.clientWidth);
              const cy = clamp(ev.clientY - rect.top, 0, over.clientHeight);
              zoomBox.style.left = Math.min(sx, cx) + "px";
              zoomBox.style.top = Math.min(sy, cy) + "px";
              zoomBox.style.width = Math.abs(cx - sx) + "px";
              zoomBox.style.height = Math.abs(cy - sy) + "px";
            }
            function boxUp(ev) {
              document.removeEventListener("mousemove", boxMove);
              document.removeEventListener("mouseup", boxUp);
              over.style.cursor = "grab";
              zoomBox.style.display = "none";
              const cx = clamp(ev.clientX - rect.left, 0, over.clientWidth);
              const cy = clamp(ev.clientY - rect.top, 0, over.clientHeight);
              // true rectangle zoom: X follows the box width, Y the box
              // height. Both axes are always set so the resulting view is
              // exactly the drawn box. Degenerate drags are ignored.
              if (Math.abs(cx - sx) < BOX_MIN_PX || Math.abs(cy - sy) < BOX_MIN_PX) return;
              const xa = u.posToVal(sx, "x"), xb = u.posToVal(cx, "x");
              const ya = u.posToVal(sy, "y"), yb = u.posToVal(cy, "y");
              setX(Math.min(xa, xb), Math.max(xa, xb));
              u.setScale("y", { min: Math.min(ya, yb), max: Math.max(ya, yb) });
            }
            document.addEventListener("mousemove", boxMove);
            document.addEventListener("mouseup", boxUp);
            return;
          }

          over.style.cursor = "grabbing";
          const startX = e.clientX, startY = e.clientY;
          const x0 = { ...u.scales.x }, y0 = { ...u.scales.y };
          const xPerPx = (x0.max - x0.min) / u.over.clientWidth;
          const yPerPx = (y0.max - y0.min) / u.over.clientHeight;

          function move(ev) {
            const dx = (ev.clientX - startX) * xPerPx;
            const dy = (ev.clientY - startY) * yPerPx;
            setX(x0.min - dx, x0.max - dx);
            u.setScale("y", { min: y0.min + dy, max: y0.max + dy });
          }
          function up() {
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", up);
            over.style.cursor = "grab";
          }
          document.addEventListener("mousemove", move);
          document.addEventListener("mouseup", up);
        });

        over.addEventListener("dblclick", () => {
          setX(xFull.min, xFull.max);
          // Honor a chart's configured Y range function (e.g. spectrum charts
          // framed to their noise band); fall back to fitting visible series.
          const rangeFn = u.scales.y.range;
          if (typeof rangeFn === "function") {
            let lo = Infinity, hi = -Infinity;
            for (let i = 1; i < u.series.length; i++) {
              if (!u.series[i].show) continue;
              const arr = u.data[i];
              for (let j = 0; arr && j < arr.length; j++) {
                const v = arr[j];
                if (v == null || Number.isNaN(v)) continue;
                if (v < lo) lo = v;
                if (v > hi) hi = v;
              }
            }
            if (lo > hi) { lo = 0; hi = 1; }
            const [ymin, ymax] = rangeFn(u, lo, hi);
            u.setScale("y", { min: ymin, max: ymax });
          } else {
            const y = visibleYExtent(u);
            u.setScale("y", { min: y.min, max: y.max });
          }
        });
      },
    },
  };
}
