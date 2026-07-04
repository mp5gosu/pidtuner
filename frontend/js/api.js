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

const DTYPES = { f32: [Float32Array, 4], f64: [Float64Array, 8] };

// Parses the binary series format from core/binary_pack.py.
export async function getGyro(logId, sessionId) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}/gyro`);
  if (!resp.ok) throw new Error((await resp.json()).detail || "gyro fetch failed");
  const buf = await resp.arrayBuffer();

  const metaLen = new DataView(buf).getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, metaLen)));

  const payloadStart = 4 + metaLen;
  const series = {};
  for (const s of meta.series) {
    const [Ctor] = DTYPES[s.dtype];
    series[s.name] = new Ctor(buf, payloadStart + s.offset, s.points);
  }
  return { series, extra: meta.extra };
}

// Parses the binary series format from core/binary_pack.py for the
// throttle x frequency maps. Grids arrive flat (row-major, freq-major) and are
// kept as flat Float32Array views plus their shape in meta.extra.
export async function getNoiseThrottle(logId, sessionId) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}/noise-throttle`);
  if (!resp.ok) throw new Error((await resp.json()).detail || "noise-throttle fetch failed");
  const buf = await resp.arrayBuffer();

  const metaLen = new DataView(buf).getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, metaLen)));

  const payloadStart = 4 + metaLen;
  const series = {};
  for (const s of meta.series) {
    const [Ctor] = DTYPES[s.dtype];
    series[s.name] = new Ctor(buf, payloadStart + s.offset, s.points);
  }
  return { series, extra: meta.extra };
}

export async function getSpectrum(logId, sessionId) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}/spectrum`);
  if (!resp.ok) throw new Error((await resp.json()).detail || "spectrum fetch failed");
  return resp.json();
}

export async function getStepResponse(logId, sessionId) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}/step-response`);
  if (!resp.ok) throw new Error((await resp.json()).detail || "step-response fetch failed");
  return resp.json();
}

// Set (or clear, with an empty name) a user-facing name for a session.
export async function renameSession(logId, sessionId, name) {
  const resp = await fetch(`/api/logs/${logId}/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || "rename failed");
  return resp.json();
}
