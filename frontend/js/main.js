import { uploadLog, getGyro, getSpectrum, getNoiseThrottle } from "./api.js";
import { renderGyro, destroyGyro } from "./charts/gyroChart.js";
import { renderSpectrum, destroySpectrum } from "./charts/spectrumChart.js";
import { renderNoiseThrottle, destroyNoiseThrottle } from "./charts/noiseThrottleChart.js";
import {
  initCompare, renderCompareTab, registerUploadedLog, selectForCompare,
  renameSessionByKey, restoreCompare, uploadedLogIds,
} from "./compare.js";
import { setSyncEnabled } from "./charts/zoomPlugin.js";
import { renderMetaSummary, clearMetaSummary } from "./metaSummary.js";
import {
  persistActive, loadLogIds, loadSelectedKeys, loadActive, clearPersisted,
} from "./persist.js";

const el = (id) => document.getElementById(id);

const state = {
  logId: null,
  sessionId: null,
  sessions: [],
  file: null,   // {name, size} of the uploaded file, for the metadata bar
  gyroLoaded: false,
  spectrumLoaded: false,
  noiseLoaded: false,
};

// ---- toast / banner ---------------------------------------------------

let toastTimer;
function toast(msg, ok = false) {
  const t = el("toast");
  t.textContent = msg;
  t.classList.toggle("ok", ok);
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 6000);
}

function setBanner(msg) {
  const b = el("banner");
  if (msg) {
    b.textContent = msg;
    b.classList.remove("hidden");
  } else {
    b.classList.add("hidden");
  }
}

// ---- upload -----------------------------------------------------------

el("upload-btn").addEventListener("click", () => el("file-input").click());

el("file-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const btn = el("upload-btn");
  const progress = el("upload-progress");
  const bar = progress.querySelector(".bar");
  const label = progress.querySelector("span");

  btn.disabled = true;
  progress.classList.remove("hidden");
  try {
    const result = await uploadLog(file, (frac) => {
      bar.style.width = `${Math.round(frac * 100)}%`;
      label.textContent = frac >= 1 ? "decoding…" : `${Math.round(frac * 100)}%`;
    });
    state.logId = result.log_id;
    state.sessions = result.sessions;
    state.file = { name: file.name, size: file.size };
    populateSessions();
    // default to the longest session - the first is often a tiny arm-blip
    const longest = [...result.sessions].sort((a, b) => b.duration_s - a.duration_s)[0];
    el("session-select").value = longest.session_id;
    await selectSession(longest.session_id);
    toast(`${file.name}: ${result.sessions.length} session(s) decoded`, true);

    // make the new log available on the step-response tab & auto-show it
    registerUploadedLog(result);
    selectForCompare(result.log_id, longest.session_id).catch((err) => toast(err.message));
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    progress.classList.add("hidden");
    bar.style.width = "0";
    e.target.value = "";
  }
});

function populateSessions() {
  const sel = el("session-select");
  sel.innerHTML = "";
  for (const s of state.sessions) {
    const opt = document.createElement("option");
    opt.value = s.session_id;
    const craft = s.headers.craft_name ? ` · ${s.headers.craft_name}` : "";
    const name = s.name || `Session ${s.session_id + 1}`;
    opt.textContent = `${name} (${s.duration_s.toFixed(1)}s${craft})`;
    sel.appendChild(opt);
  }
  if (state.sessionId != null) sel.value = state.sessionId;
  // kept hidden while the inline rename editor has replaced the dropdown
  const show = state.sessions.length > 0 && !renaming;
  sel.classList.toggle("hidden", !show);
  el("session-rename-btn").classList.toggle("hidden", !show);
}

// A session renamed on the Step-Response tab may belong to the active log;
// its session objects are shared, so re-render the dropdown labels.
document.addEventListener("session-renamed", () => {
  if (state.sessions.length) populateSessions();
});

// ---- rename the active session from the Gyro-tab header -----------------

let renaming = false;

el("session-rename-btn").addEventListener("click", startHeaderRename);

function startHeaderRename() {
  if (state.logId == null || state.sessionId == null || renaming) return;
  const session = state.sessions.find((s) => s.session_id === state.sessionId);
  if (!session) return;
  const sel = el("session-select");

  const input = document.createElement("input");
  input.type = "text";
  input.className = "rename-input";
  input.value = session.name || "";
  input.placeholder = `Session ${state.sessionId + 1}`;

  renaming = true;
  sel.classList.add("hidden");
  el("session-rename-btn").classList.add("hidden");
  sel.after(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim() !== (session.name || "")) {
      try {
        await renameSessionByKey(state.logId, state.sessionId, input.value);
      } catch (e) {
        toast(e.message);
      }
    }
    renaming = false;
    input.remove();
    populateSessions();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

el("session-select").addEventListener("change", (e) => {
  selectSession(parseInt(e.target.value, 10));
});

async function selectSession(sessionId) {
  state.sessionId = sessionId;
  persistActive({ logId: state.logId, sessionId, file: state.file });
  state.gyroLoaded = false;
  state.spectrumLoaded = false;
  state.noiseLoaded = false;
  destroyGyro();
  destroySpectrum();
  destroyNoiseThrottle();
  el("empty-state").classList.add("hidden");

  const session = state.sessions.find((s) => s.session_id === sessionId);
  if (session) {
    renderMetaSummary(session, state.file);
  } else {
    clearMetaSummary();
  }
  if (session && !session.gyro_unfilt_available) {
    setBanner(
      "This log contains no unfiltered gyro signal. In the Betaflight CLI, set " +
      "`set debug_mode = GYRO_SCALED` (and `save`) before recording your next log " +
      "to enable the filtered/unfiltered comparison."
    );
  } else {
    setBanner(null);
  }
  await loadActiveTab();
}

// ---- tabs -------------------------------------------------------------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    el(`tab-${tab.dataset.tab}`).classList.add("active");
    loadActiveTab();
  });
});

function activeTab() {
  return document.querySelector(".tab.active").dataset.tab;
}

async function loadActiveTab() {
  const tab = activeTab();
  if (tab === "compare") {
    el("empty-state").classList.add("hidden");
    renderCompareTab();
    return;
  }
  if (state.logId == null || state.sessionId == null) return;
  try {
    if (tab === "gyro" && !state.gyroLoaded) {
      const data = await getGyro(state.logId, state.sessionId);
      renderGyro(el("gyro-charts"), data);
      state.gyroLoaded = true;
    } else if (tab === "spectrum" && !state.spectrumLoaded) {
      const data = await getSpectrum(state.logId, state.sessionId);
      renderSpectrum(el("spectrum-charts"), data);
      state.spectrumLoaded = true;
    } else if (tab === "noise" && !state.noiseLoaded) {
      const data = await getNoiseThrottle(state.logId, state.sessionId);
      renderNoiseThrottle(el("noise-charts"), data);
      state.noiseLoaded = true;
    }
  } catch (err) {
    toast(err.message);
  }
}

initCompare(toast);

// ---- axis-sync toggle (default off, persisted) --------------------------

const syncToggle = el("sync-toggle");
syncToggle.checked = localStorage.getItem("pidtuner-axis-sync") === "1";
setSyncEnabled(syncToggle.checked);
syncToggle.addEventListener("change", () => {
  setSyncEnabled(syncToggle.checked);
  localStorage.setItem("pidtuner-axis-sync", syncToggle.checked ? "1" : "0");
});

// ---- keepalive heartbeat -----------------------------------------------
// While this tab is open and visible, tell the server its uploads are still in
// use so the inactivity reaper (DATA_TTL_MIN) only claims abandoned logs. A
// closed/hidden tab stops beating and is reaped after the TTL.

const HEARTBEAT_MS = 5 * 60 * 1000;
function heartbeat() {
  if (document.visibilityState !== "visible") return;
  for (const id of uploadedLogIds()) {
    fetch(`/api/logs/${id}/keepalive`, { method: "POST", keepalive: true }).catch(() => {});
  }
}
setInterval(heartbeat, HEARTBEAT_MS);
document.addEventListener("visibilitychange", heartbeat);

// ---- restore after reload ----------------------------------------------
// Re-attach to logs still alive on the server so an accidental refresh isn't a
// total loss. Anything already reaped is dropped from local storage.

async function restoreSession() {
  const ids = loadLogIds();
  if (!ids.length) return;

  const indexes = [];
  for (const id of ids) {
    try {
      const resp = await fetch(`/api/logs/${id}/sessions`);
      if (resp.ok) indexes.push(await resp.json());
    } catch { /* unreachable - skip */ }
  }
  if (!indexes.length) { clearPersisted(); return; }

  el("empty-state").classList.add("hidden");

  // Step-Response tab: re-register logs and re-select the compared sessions.
  await restoreCompare(indexes, loadSelectedKeys());

  // Main tabs: restore the active log/session (fall back to the newest log).
  const active = loadActive();
  const activeIdx = (active && indexes.find((l) => l.log_id === active.logId))
    || indexes[indexes.length - 1];
  state.logId = activeIdx.log_id;
  state.sessions = activeIdx.sessions;
  state.file = (active && active.logId === activeIdx.log_id) ? active.file : null;
  populateSessions();
  const known = active && active.logId === activeIdx.log_id
    && activeIdx.sessions.some((s) => s.session_id === active.sessionId);
  const sid = known
    ? active.sessionId
    : [...activeIdx.sessions].sort((a, b) => b.duration_s - a.duration_s)[0].session_id;
  el("session-select").value = sid;
  await selectSession(sid);

  heartbeat();
}

restoreSession().catch((err) => toast(err.message));
