# syntax=docker/dockerfile:1

# --- aMule stage: build amuled + amuleapi from a pinned git commit, on the runtime's Debian ---
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS amule

# The single source of truth for the aMule version: the bump workflow rewrites these two lines.
ARG AMULE_VERSION=3.1.0
ARG AMULE_COMMIT=909d304d993ee07df6c6f6acf501a6d791d53666

# libwxgtk3.2-dev even for a daemon: trixie has no wxbase dev package. Its wx-config defines
# __WXGTK__, which makes aMule's CMake require glib. git lets CMake read the version from the tag.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         cmake g++ make pkgconf python3 git \
         libwxgtk3.2-dev libcrypto++-dev libboost-dev zlib1g-dev libcurl4-openssl-dev libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Tags are unsigned and can move: the checksum pins the peeled commit, and a version bumped
# without its commit fails here because the tag resolves to another one.
ADD --keep-git-dir=true --checksum=${AMULE_COMMIT} \
    https://github.com/amule-org/amule.git#${AMULE_VERSION} /src/amule

RUN cmake -S /src/amule -B /build -DCMAKE_BUILD_TYPE=Release \
         -DBUILD_MONOLITHIC=OFF -DBUILD_DAEMON=ON -DBUILD_AMULEAPI=ON -DBUILD_ED2K=OFF \
         -DENABLE_VERSION_CHECK=OFF -DENABLE_UPNP=OFF -DENABLE_IP2COUNTRY=OFF \
         -DENABLE_NLS=OFF -DENABLE_BFD=OFF -DENABLE_CCACHE=OFF \
    && cmake --build /build -j"$(nproc)" \
    && DESTDIR=/out cmake --install /build --strip

# aMule has no package metadata for Syft to find: declare it, from the same ARG as the source.
COPY packages/crawler/docker/amule.cdx.json /tmp/amule.cdx.json
RUN sed "s/@AMULE_VERSION@/${AMULE_VERSION}/g" /tmp/amule.cdx.json \
      > /out/usr/local/share/amule/amule.cdx.json

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
    uv sync --frozen --no-install-workspace --package mulewatch

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package mulewatch

# --- Runtime: one image, two supervised services (amuled itself starts amuleapi) ---
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

# ARG after FROM only lives in the stage that declares it, hence the re-declaration.
ARG VERSION=0.0.0
LABEL org.opencontainers.image.version=${VERSION}

# s6 comes from Debian, not from the s6-overlay tarball, so dpkg knows about it and the
# security feed covers it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 s6 \
         libwxbase3.2-1t64 libcrypto++8t64 libglib2.0-0t64 libcurl4t64 zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=amule /out/usr/local/bin/amuled /out/usr/local/bin/amuleapi /usr/local/bin/
# amuleapi serves its web UI from this compiled-in path; the SBOM file sits beside it.
COPY --from=amule /out/usr/local/share/amule/ /usr/local/share/amule/
COPY --from=amule /src/amule/LICENSE.md /usr/local/share/doc/amule/LICENSE.md

COPY --from=python /app/.venv /app/.venv
COPY packages/crawler/docker/entrypoint.sh /usr/local/bin/
COPY packages/crawler/docker/services.d/ /etc/services.d/

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
