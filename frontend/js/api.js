// fetch/XHR wrappers for the backend API.

export function uploadLog(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/logs");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail; } catch {}
        reject(new Error(detail || `Upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

// Pull a useful message out of an error response, tolerating non-JSON bodies
// (a proxy 502 HTML page, a gateway timeout, an empty body) that would
// otherwise make resp.json() throw and mask the real failure.
async function errText(resp, fallback) {
  try {
    const data = await resp.json();
    if (data && data.detail) return data.detail;
  } catch { /* body wasn't JSON */ }
  return resp.statusText || fallback || `Request failed (${resp.status})`;
}

const DTYPES = { f32: [Float32Array, 4], f64: [Float64Array, 8] };

// Parse the binary series format from core/binary_pack.py into zero-copy typed
// arrays over the response buffer. Every offset/length is bounds-checked so a
// malformed or truncated payload throws a clear error instead of a RangeError
// deep in the view constructor. Grids arrive flat (row-major) and are reshaped
// client-side from meta.extra.
async function fetchBinarySeries(path, label) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(await errText(resp, `${label} fetch failed`));
  const buf = await resp.arrayBuffer();
  if (buf.byteLength < 4) throw new Error(`${label}: response too short`);

  const metaLen = new DataView(buf).getUint32(0, true);
  if (4 + metaLen > buf.byteLength) throw new Error(`${label}: truncated header`);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, metaLen)));

  const payloadStart = 4 + metaLen;
  const series = {};
  for (const s of meta.series || []) {
    const spec = DTYPES[s.dtype];
    if (!spec) throw new Error(`${label}: unknown dtype ${s.dtype}`);
    const [Ctor, bytes] = spec;
    const start = payloadStart + s.offset;
    if (s.offset < 0 || start + s.points * bytes > buf.byteLength) {
      throw new Error(`${label}: series ${s.name} out of bounds`);
    }
    series[s.name] = new Ctor(buf, start, s.points);
  }
  return { series, extra: meta.extra };
}

export function getGyro(logId, sessionId) {
  return fetchBinarySeries(`/api/logs/${logId}/sessions/${sessionId}/gyro`, "gyro");
}

export function getNoiseThrottle(logId, sessionId) {
  return fetchBinarySeries(
    `/api/logs/${logId}/sessions/${sessionId}/noise-throttle`, "noise-throttle");
}

export async function getSpectrum(logId, sessionId) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}/spectrum`);
  if (!resp.ok) throw new Error(await errText(resp, "spectrum fetch failed"));
  return resp.json();
}

export async function getStepResponse(logId, sessionId) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}/step-response`);
  if (!resp.ok) throw new Error(await errText(resp, "step-response fetch failed"));
  return resp.json();
}

// Set (or clear, with an empty name) a user-facing name for a session.
export async function renameSession(logId, sessionId, name) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!resp.ok) throw new Error(await errText(resp, "rename failed"));
  return resp.json();
}
