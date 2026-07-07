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
# Pinned commit for reproducible, tamper-evident builds of a parser that runs on
# untrusted log data (keep in sync with scripts/build_blackbox_tools.sh).
ARG BBT_REF=af5c31ab9ab62b93083d6d355043026a76ce4eee
# Only the decode tool is needed; the cairo/pkg-config errors from the
# Makefile are harmless (cairo is only used by blackbox_render).
RUN git clone https://github.com/betaflight/blackbox-tools.git . \
    && git checkout --quiet ${BBT_REF} \
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

# Dependency layer first (cached until pyproject/lock change). The committed
# uv.lock pins exact transitive versions; --frozen fails the build if it drifts
# from pyproject.toml rather than silently re-resolving.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --no-dev --no-install-project --frozen

# Application code + prebuilt decoder binary
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY --from=blackbox-builder /build/obj/blackbox_decode /app/backend/bin/blackbox_decode

# Ephemeral scratch dir for decoding (wiped on startup; no VOLUME, so nothing
# persists after the container is removed).
RUN mkdir -p /data

# Drop privileges: uvicorn + the blackbox_decode subprocess (fed untrusted log
# data) should not run as root. Only /data is written at runtime.
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home app \
    && chown -R app:app /data
USER app

EXPOSE 8000

# ONE worker only — all caches (log registry, DataFrame LRU, session store)
# are process-local; --workers > 1 would break them.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
