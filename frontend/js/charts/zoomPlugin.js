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

        // The cursor stays at its default until a drag starts: "grabbing" while
        // panning, "crosshair" while alt box-zooming, restored on mouseup. This
        // way the hand only shows up when something is actually being dragged.
        // touch-action pan-y lets one finger scroll the page while two-finger
        // gestures (pan+zoom) and double-tap reset are handled below.
        over.style.touchAction = "pan-y";

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
              over.style.cursor = "";
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
            over.style.cursor = "";
          }
          document.addEventListener("mousemove", move);
          document.addEventListener("mouseup", up);
        });

        // Reset to the full view (X to data extent, Y to the chart's configured
        // range function if any, else the visible-series extent). Shared by
        // double-click (mouse) and double-tap (touch).
        function resetView() {
          setX(xFull.min, xFull.max);
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
        }

        over.addEventListener("dblclick", resetView);

        // ---- touch: two-finger pan+zoom, double-tap reset ------------------
        // One finger scrolls the page (touch-action: pan-y). Two fingers pin
        // each finger's data value under its pixel, so the same gesture pans
        // and zooms both axes at once (val = c0 + c1·px, solved per axis).
        let touch = null;
        let lastTap = 0;
        const tPos = (t) => {
          const r = over.getBoundingClientRect();
          return { px: t.clientX - r.left, py: t.clientY - r.top };
        };

        over.addEventListener("touchstart", (e) => {
          if (e.touches.length === 2) {
            const A = tPos(e.touches[0]), B = tPos(e.touches[1]);
            touch = {
              mode: "pinch",
              pxA: A.px, pxB: B.px, pyA: A.py, pyB: B.py,
              vxA: u.posToVal(A.px, "x"), vxB: u.posToVal(B.px, "x"),
              vyA: u.posToVal(A.py, "y"), vyB: u.posToVal(B.py, "y"),
            };
            e.preventDefault();
          } else if (e.touches.length === 1) {
            const t = e.touches[0];
            touch = { mode: "tap", sx: t.clientX, sy: t.clientY, moved: false };
          }
        }, { passive: false });

        over.addEventListener("touchmove", (e) => {
          if (!touch) return;
          if (touch.mode === "pinch" && e.touches.length === 2) {
            e.preventDefault();
            const A = tPos(e.touches[0]), B = tPos(e.touches[1]);
            const W = over.clientWidth, H = over.clientHeight;
            if (Math.abs(A.px - B.px) > 20) {
              const c1 = (touch.vxA - touch.vxB) / (A.px - B.px);
              const c0 = touch.vxA - c1 * A.px;
              setX(c0, c0 + c1 * W);
            }
            if (Math.abs(A.py - B.py) > 20) {
              const c1 = (touch.vyA - touch.vyB) / (A.py - B.py);
              const c0 = touch.vyA - c1 * A.py;
              u.setScale("y", { min: c0 + c1 * H, max: c0 });
            }
          } else if (touch.mode === "tap") {
            const t = e.touches[0];
            if (Math.abs(t.clientX - touch.sx) > 8 || Math.abs(t.clientY - touch.sy) > 8) {
              touch.moved = true; // it's a page scroll, not a tap
            }
          }
        }, { passive: false });

        over.addEventListener("touchend", (e) => {
          if (e.touches.length > 0) return; // fingers still down
          const wasTap = touch && touch.mode === "tap" && !touch.moved;
          touch = null;
          if (!wasTap) return;
          const now = Date.now();
          if (now - lastTap < 300) { resetView(); lastTap = 0; }
          else lastTap = now;
        }, { passive: false });
      },
    },
  };
}
