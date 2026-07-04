// Noise-vs-throttle tab: per-axis throttle x frequency amplitude heatmaps
// (PIDtoolbox's "throttle x frequency spectrogram"). One canvas heatmap per
// axis; a shared switcher picks the signal (raw vs filtered gyro), a slider
// sets the colour-scale ceiling, and a "<100 Hz" toggle zooms the frequency
// band. Throttle-dependent motor/frame resonances show up as diagonal ridges,
// so you can see which throttle band a peak lives in.
//
// uPlot has no heatmap, so this draws everything (image, axes, colourbar,
// crosshair) onto a canvas itself. The maximize behaviour is reused from
// uplotSetup so it feels like the other tabs.

import { makeMaximizable, closeMaximized } from "./uplotSetup.js";

const AXES = ["roll", "pitch", "yaw"];

const SIGNALS = [
  { key: "unfiltered", label: "Gyro (raw)" },
  { key: "filtered", label: "Gyro (filtered)" },
];

const M = { l: 54, r: 82, t: 22, b: 40 };   // plot margins (css px)
const HEIGHT = 300;
const COLORS = {
  bg: "#1c2129", axis: "#8b96a5", text: "#d7dee8",
  grid: "rgba(255,255,255,0.10)", crosshair: "rgba(255,255,255,0.55)",
};

let charts = [];         // [{ axis, draw, resize, destroy }]
let controlsEl = null;
let data = null;         // { series, extra }
let currentSignal = "unfiltered";
let freqMax = null;      // null = full nyquist; otherwise a cap in Hz
let scaleMult = 1;       // colour-scale ceiling = vmax * scaleMult

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// Classic "hot" colormap (black -> red -> yellow -> white), t in [0, 1].
function hot(t) {
  t = clamp(t, 0, 1);
  return [
    clamp(t * 3, 0, 1) * 255,
    clamp(t * 3 - 1, 0, 1) * 255,
    clamp(t * 3 - 2, 0, 1) * 255,
  ];
}

// Nice round tick values from 0..max, roughly `approx` of them.
function axisTicks(max, approx = 5) {
  if (!(max > 0)) return [0];
  const raw = max / approx;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const ticks = [];
  for (let v = 0; v <= max + step * 1e-6; v += step) ticks.push(v);
  return ticks;
}

// ---- one axis heatmap ---------------------------------------------------

function createHeatmap(container, axis) {
  const box = document.createElement("div");
  box.className = "chart-box";
  const h = document.createElement("h3");
  box.appendChild(h);
  makeMaximizable(box);

  const el = document.createElement("div");
  el.className = "chart-el nt-el";
  const canvas = document.createElement("canvas");
  const tip = document.createElement("div");
  tip.className = "nt-tip hidden";
  el.append(canvas, tip);
  box.appendChild(el);
  container.appendChild(box);

  const ctx = canvas.getContext("2d");
  let cssW = 0, cssH = HEIGHT;
  let off = null, offSig = null;   // cached native-res image + what it was built from
  let plot = null;                 // last plot rect, for hit-testing

  function meta() {
    const e = data.extra.axes[axis]?.signals?.[currentSignal];
    return e || null;
  }
  function grid() {
    return data.series[`${axis}.${currentSignal}`] || null;
  }
  const freqArr = () => data.series.freq;

  // Row index of the highest frequency still shown (<= freqMax).
  function topRow() {
    const nF = data.extra.n_freq, nyq = data.extra.nyquist;
    if (freqMax == null) return nF - 1;
    const step = nyq / (nF - 1);
    return clamp(Math.floor(freqMax / step), 1, nF - 1);
  }

  function vmaxEff() {
    return data.extra.vmax * scaleMult;
  }

  // (Re)build the native-resolution image (throttle x freq) for the current
  // signal / scale / band. Cheap (~12k px) so we just rebuild on any change.
  function buildImage() {
    const g = grid();
    const nT = data.extra.n_throttle;
    const kMax = topRow();
    const rows = kMax + 1;
    off = document.createElement("canvas");
    off.width = nT;
    off.height = rows;
    const octx = off.getContext("2d");
    const img = octx.createImageData(nT, rows);
    const px = img.data;
    const vm = vmaxEff() || 1;
    for (let f = 0; f <= kMax; f++) {
      const imgRow = kMax - f;                 // high freq on top
      const base = f * nT;
      const rowOff = imgRow * nT * 4;
      for (let x = 0; x < nT; x++) {
        const [r, gg, b] = hot(g[base + x] / vm);
        const p = rowOff + x * 4;
        px[p] = r; px[p + 1] = gg; px[p + 2] = b; px[p + 3] = 255;
      }
    }
    octx.putImageData(img, 0, 0);
    offSig = `${currentSignal}|${freqMax}|${scaleMult}`;
  }

  function yTop() {
    return freqArr()[topRow()];
  }

  function draw(cross = null) {
    if (!data || !grid()) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, cssW, cssH);

    const x0 = M.l, y0 = M.t, x1 = cssW - M.r, y1 = cssH - M.b;
    const pw = x1 - x0, ph = y1 - y0;
    if (pw <= 10 || ph <= 10) return;
    plot = { x0, y0, x1, y1, pw, ph };

    if (!off || offSig !== `${currentSignal}|${freqMax}|${scaleMult}`) buildImage();

    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(off, x0, y0, pw, ph);

    const fTop = yTop();
    const fTicks = axisTicks(fTop, 5);
    const tTicks = [0, 20, 40, 60, 80, 100];

    // faint gridlines over the image
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (const f of fTicks) {
      const y = Math.round(y1 - (f / fTop) * ph) + 0.5;
      ctx.moveTo(x0, y); ctx.lineTo(x1, y);
    }
    for (const t of tTicks) {
      const x = Math.round(x0 + (t / 100) * pw) + 0.5;
      ctx.moveTo(x, y0); ctx.lineTo(x, y1);
    }
    ctx.stroke();

    // frame
    ctx.strokeStyle = COLORS.axis;
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, pw, ph);

    // ticks + labels
    ctx.fillStyle = COLORS.axis;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const f of fTicks) {
      const y = y1 - (f / fTop) * ph;
      ctx.fillText(String(Math.round(f)), x0 - 6, y);
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const t of tTicks) {
      const x = x0 + (t / 100) * pw;
      ctx.fillText(String(t), x, y1 + 6);
    }

    // axis titles
    ctx.fillStyle = COLORS.text;
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    ctx.fillText("% Throttle", (x0 + x1) / 2, cssH - 6);
    ctx.save();
    ctx.translate(12, (y0 + y1) / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("Freq. (Hz)", 0, 0);
    ctx.restore();

    // mean / peak annotation (like PIDtoolbox)
    const m = meta();
    if (m) {
      ctx.fillStyle = COLORS.text;
      ctx.font = "600 11px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "top";
      ctx.fillText(`mean ${m.mean.toFixed(3)}`, x1 - 4, y0 + 3);
      ctx.fillText(`peak ${m.peak.toFixed(3)}`, x1 - 4, y0 + 16);
    }

    drawColorbar(x1 + 16, y0, ph);

    if (cross) drawCrosshair(cross, plot, fTop);
  }

  function drawColorbar(cbx, cby, ph) {
    const cbw = 12;
    for (let i = 0; i < ph; i++) {
      const [r, g, b] = hot((ph - i) / ph);
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fillRect(cbx, cby + i, cbw, 1);
    }
    ctx.strokeStyle = COLORS.axis;
    ctx.strokeRect(cbx + 0.5, cby + 0.5, cbw, ph);
    const vm = vmaxEff();
    ctx.fillStyle = COLORS.axis;
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (const frac of [0, 0.5, 1]) {
      const y = cby + ph - frac * ph;
      ctx.fillText((vm * frac).toFixed(2), cbx + cbw + 4, y);
    }
    ctx.textBaseline = "bottom";
    ctx.fillText("°/s", cbx + cbw + 4, cby - 3);
  }

  function drawCrosshair(cross, p, fTop) {
    const x = p.x0 + (cross.thr / 100) * p.pw;
    const y = p.y1 - (cross.freq / fTop) * p.ph;
    ctx.strokeStyle = COLORS.crosshair;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(p.x0, y + 0.5); ctx.lineTo(p.x1, y + 0.5);
    ctx.moveTo(x + 0.5, p.y0); ctx.lineTo(x + 0.5, p.y1);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // ---- hover readout ----
  canvas.addEventListener("mousemove", (ev) => {
    if (!plot || !grid()) return;
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    if (mx < plot.x0 || mx > plot.x1 || my < plot.y0 || my > plot.y1) {
      tip.classList.add("hidden"); draw(); return;
    }
    const fTop = yTop();
    const thr = clamp(((mx - plot.x0) / plot.pw) * 100, 0, 100);
    const freq = clamp(((plot.y1 - my) / plot.ph) * fTop, 0, fTop);
    const nT = data.extra.n_throttle, nF = data.extra.n_freq;
    const nyq = data.extra.nyquist;
    const ti = clamp(Math.floor((thr / 100) * nT), 0, nT - 1);
    const fi = clamp(Math.round((freq / nyq) * (nF - 1)), 0, nF - 1);
    const v = grid()[fi * nT + ti];
    draw({ thr, freq });
    tip.classList.remove("hidden");
    tip.textContent = `${Math.round(thr)}% · ${Math.round(freqArr()[fi])} Hz · ${v.toFixed(3)} °/s`;
    tip.style.left = clamp(mx + 12, 0, cssW - 130) + "px";
    tip.style.top = clamp(my + 12, 0, cssH - 30) + "px";
  });
  canvas.addEventListener("mouseleave", () => { tip.classList.add("hidden"); draw(); });

  function resize() {
    const w = el.clientWidth;
    if (w <= 0) return;
    cssW = w;
    cssH = box.classList.contains("maximized") ? Math.max(240, el.clientHeight) : HEIGHT;
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
    off = null;   // force rebuild at draw
    draw();
  }

  const ro = new ResizeObserver(resize);
  ro.observe(el);

  function refreshTitle() {
    const sigLabel = SIGNALS.find((s) => s.key === currentSignal)?.label ?? "";
    h.textContent = `${axis} — ${sigLabel}`;
  }
  refreshTitle();

  return {
    axis,
    draw: () => { refreshTitle(); off = null; draw(); },
    resize,
    destroy: () => {
      if (box.classList.contains("maximized")) closeMaximized();
      ro.disconnect();
      box.remove();
    },
  };
}

// ---- render + shared controls -------------------------------------------

export function renderNoiseThrottle(container, payload) {
  destroyNoiseThrottle();
  data = payload;

  // pick a sensible default signal that exists in this log
  const has = (key) => AXES.some((a) => data.series[`${a}.${key}`]);
  if (!has(currentSignal)) currentSignal = has("unfiltered") ? "unfiltered" : "filtered";

  for (const axis of AXES) {
    if (!SIGNALS.some((s) => data.series[`${axis}.${s.key}`])) continue;
    charts.push(createHeatmap(container, axis));
  }
  buildControls();
}

function redrawAll() {
  for (const c of charts) c.draw();
}

function buildControls() {
  controlsEl = document.getElementById("noise-controls");
  if (!controlsEl) return;
  controlsEl.innerHTML = "";

  // signal switcher (segmented) — only signals actually present
  const seg = document.createElement("div");
  seg.className = "nt-seg";
  const present = SIGNALS.filter((s) => AXES.some((a) => data.series[`${a}.${s.key}`]));
  for (const s of present) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "nt-seg-btn" + (s.key === currentSignal ? " active" : "");
    b.textContent = s.label;
    b.addEventListener("click", () => {
      currentSignal = s.key;
      seg.querySelectorAll(".nt-seg-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      redrawAll();
    });
    seg.appendChild(b);
  }
  controlsEl.appendChild(seg);

  // <100 Hz band toggle
  const bandLbl = document.createElement("label");
  bandLbl.className = "signal-toggle";
  const bandCb = document.createElement("input");
  bandCb.type = "checkbox";
  bandCb.checked = freqMax != null;
  bandCb.addEventListener("change", () => {
    freqMax = bandCb.checked ? 100 : null;
    redrawAll();
  });
  bandLbl.append(bandCb, document.createTextNode("< 100 Hz"));
  controlsEl.appendChild(bandLbl);

  // colour-scale slider
  const scaleLbl = document.createElement("label");
  scaleLbl.className = "signal-toggle nt-scale";
  const scaleVal = document.createElement("span");
  scaleVal.className = "nt-scale-val";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "-2"; slider.max = "2"; slider.step = "0.1";  // 2^x, 0.25x..4x
  slider.value = String(Math.log2(scaleMult));
  const showScale = () => { scaleVal.textContent = `${scaleMult.toFixed(2)}×`; };
  slider.addEventListener("input", () => {
    scaleMult = Math.pow(2, parseFloat(slider.value));
    showScale();
    redrawAll();
  });
  showScale();
  scaleLbl.append(document.createTextNode("Scale"), slider, scaleVal);
  controlsEl.appendChild(scaleLbl);
}

export function destroyNoiseThrottle() {
  charts.forEach((c) => c.destroy());
  charts = [];
  if (controlsEl) controlsEl.innerHTML = "";
  data = null;
}

// keep canvases crisp on window resize (ResizeObserver covers layout changes,
// but not devicePixelRatio-only changes such as moving between monitors)
window.addEventListener("resize", () => charts.forEach((c) => c.resize()));
