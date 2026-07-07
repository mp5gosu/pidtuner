// Shared tiny helpers.

const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

// Escape a string for safe interpolation into innerHTML. Use for ANY
// user-controlled text (session names, uploaded filenames, blackbox header
// values like craft_name) before templating it into markup.
export function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}
