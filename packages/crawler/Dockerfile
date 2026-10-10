# syntax=docker/dockerfile:1

# Keep the stage: Dependabot only reads FROM lines, so inlining it as a COPY --from=<image>
# would freeze this digest with no update path.
FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv

# --- Python stage: build /app/.venv against Debian's python3, the same one the runtime holds ---
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS python

# Product version, computed from the git tag on the CI runner and injected here. The build
# context has no .git (.dockerignore excludes it), so hatch-vcs/setuptools-scm reads
# SETUPTOOLS_SCM_PRETEND_VERSION instead when the member wheel is built below. The same value
# stamps org.opencontainers.image.version on the runtime stage.
ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION} \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Deps layer: install straight from uv.lock, independent of the sources. --frozen skips lock
# revalidation, so it needs only uv.lock + the root pyproject.toml, not each member's
# pyproject.toml (the vex_guards and amule_bump tooling packages are never installed). The --locked
# sync below, after COPY . /app, revalidates the lock against every workspace member.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace --package p2pwatch

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package p2pwatch

# --- Runtime: the crawler and its webui, restarted by Docker, not by a supervisor ---
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

# ARG after FROM only lives in the stage that declares it, hence the re-declaration.
ARG VERSION=0.0.0
LABEL org.opencontainers.image.version=${VERSION}

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
# Explicit paths: the CLI defaults are relative to a repo checkout.
CMD ["python", "-m", "p2pwatch", \
     "--config", "/app/config/crawler.yml", \
     "--targets", "/app/config/targets.yml", \
     "--matcher", "/app/config/matcher.yml"]
