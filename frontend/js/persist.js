// localStorage persistence so an accidental page reload isn't a total loss.
// Only IDs + selections are stored; the actual data is re-fetched from the
// server on restore (the server keeps a log for DATA_TTL_MIN of inactivity and
// open tabs send a keepalive heartbeat). Everything here is best-effort —
// localStorage may be full, disabled, or unavailable in private mode.

const K_LOGS = "pidtuner-logs";         // [log_id, …] uploaded by this browser
const K_SELECTED = "pidtuner-selected"; // ["log_id:sid", …] compared sessions
const K_ACTIVE = "pidtuner-active";     // {logId, sessionId, file} for the main tabs

function read(key, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem(key));
    return v == null ? fallback : v;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}

function wipe(key) {
  try { localStorage.removeItem(key); } catch { /* ignore */ }
}

// Step-Response tab state (owned by compare.js).
export function persistCompare(logIds, selectedKeys) {
  write(K_LOGS, logIds);
  write(K_SELECTED, selectedKeys);
}
export function loadLogIds() {
  const v = read(K_LOGS, []);
  return Array.isArray(v) ? v : [];
}
export function loadSelectedKeys() {
  const v = read(K_SELECTED, []);
  return Array.isArray(v) ? v : [];
}

// Active log/session for the main (Gyro/Spectrum/Noise) tabs (owned by main.js).
export function persistActive(active) {
  active ? write(K_ACTIVE, active) : wipe(K_ACTIVE);
}
export function loadActive() {
  return read(K_ACTIVE, null);
}

export function clearPersisted() {
  wipe(K_LOGS);
  wipe(K_SELECTED);
  wipe(K_ACTIVE);
}
