# syntax=docker/dockerfile:1

##############################################################################
# Stage 1 — build the official betaflight blackbox_decode binary
# (glibc/bookworm so it is ABI-compatible with the python:*-bookworm runtime)
##############################################################################
FROM debian:bookworm-slim AS blackbox-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates make gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# Only the decode tool is needed; the cairo/pkg-config errors from the
# Makefile are harmless (cairo is only used by blackbox_render).
RUN git clone --depth 1 https://github.com/betaflight/blackbox-tools.git . \
    && make obj/blackbox_decode \
    && ./obj/blackbox_decode --help 2>&1 | grep -q Blackbox

##############################################################################
# Stage 2 — runtime
##############################################################################
FROM python:3.11-slim-bookworm AS runtime

# uv for dependency management (pinned tag for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=/usr/local/bin/python3.11 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/backend/.venv/bin:$PATH" \
    PIDTUNER_BLACKBOX_DECODE_BIN=/app/backend/bin/blackbox_decode \
    PIDTUNER_DATA_DIR=/data \
    PIDTUNER_FRONTEND_DIR=/app/frontend

WORKDIR /app/backend

# Dependency layer first (cached until pyproject/lock change).
# The uv.loc[k] glob makes the lock optional — a fresh git clone omits it
# (it is .gitignored) and uv then resolves from pyproject.toml.
COPY backend/pyproject.toml backend/uv.loc[k] ./
RUN uv sync --no-dev --no-install-project

# Application code + prebuilt decoder binary
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY --from=blackbox-builder /build/obj/blackbox_decode /app/backend/bin/blackbox_decode

# Persistent decode/dedup cache lives here (mount a volume on it)
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# ONE worker only — all caches (log registry, DataFrame LRU, session store)
# are process-local; --workers > 1 would break them.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
