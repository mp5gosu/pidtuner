#!/usr/bin/env bash
# Builds blackbox_decode from the vendored betaflight/blackbox-tools and
# installs it into backend/bin/. Requires: git, make, gcc.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor/blackbox-tools"

if [ ! -f "$VENDOR/Makefile" ]; then
    git clone --depth 1 https://github.com/betaflight/blackbox-tools.git "$VENDOR"
fi

# Only the decode tool is needed; the cairo pkg-config errors from the
# Makefile are harmless (cairo is only used by blackbox_render).
make -C "$VENDOR" obj/blackbox_decode

mkdir -p "$ROOT/backend/bin"
cp "$VENDOR/obj/blackbox_decode" "$ROOT/backend/bin/blackbox_decode"
chmod +x "$ROOT/backend/bin/blackbox_decode"

"$ROOT/backend/bin/blackbox_decode" --help >/dev/null 2>&1 || {
    echo "built binary failed its smoke test" >&2; exit 1;
}
echo "OK: backend/bin/blackbox_decode"
