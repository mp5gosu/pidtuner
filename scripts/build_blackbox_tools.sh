#!/usr/bin/env bash
# Builds blackbox_decode from the vendored betaflight/blackbox-tools and
# installs it into backend/bin/. Requires: git, make, gcc.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor/blackbox-tools"

# Pinned for reproducible, tamper-evident builds: this parser runs on untrusted
# log data, so decoder version drift must be a deliberate bump, not automatic.
# Keep in sync with the BBT_REF ARG in the Dockerfile.
BBT_REF="af5c31ab9ab62b93083d6d355043026a76ce4eee"

if [ ! -f "$VENDOR/Makefile" ]; then
    git clone https://github.com/betaflight/blackbox-tools.git "$VENDOR"
    git -C "$VENDOR" checkout --quiet "$BBT_REF"
fi

# Only the decode tool is needed; the cairo pkg-config errors from the
# Makefile are harmless (cairo is only used by blackbox_render).
make -C "$VENDOR" obj/blackbox_decode

mkdir -p "$ROOT/backend/bin"
cp "$VENDOR/obj/blackbox_decode" "$ROOT/backend/bin/blackbox_decode"
chmod +x "$ROOT/backend/bin/blackbox_decode"

# Smoke-test on OUTPUT, not exit code: blackbox_decode exits non-zero on --help
# (255) even when healthy, and `set -o pipefail` would trip on that in a pipe.
help_out="$("$ROOT/backend/bin/blackbox_decode" --help 2>&1 || true)"
if ! grep -q Blackbox <<<"$help_out"; then
    echo "built binary failed its smoke test" >&2; exit 1;
fi
echo "OK: backend/bin/blackbox_decode"
