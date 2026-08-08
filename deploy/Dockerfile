# StockWatcher Web internal test image.
# Base images and uv are pinned by digest for reproducible builds.
FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS builder

ARG UV_VERSION=0.9.26
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy
WORKDIR /app

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra server

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    TZ=Asia/Shanghai

# Noto CJK from the distribution repository; no font file is bundled.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources \
    && python -m pip uninstall --yes pip \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        ca-certificates fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 stockwatcher \
    && useradd --uid 10001 --gid stockwatcher --create-home --home-dir /home/stockwatcher stockwatcher \
    && mkdir -p /app /var/lib/stockwatcher/db /var/lib/stockwatcher/reports /backups \
    && chown -R stockwatcher:stockwatcher /app /var/lib/stockwatcher /backups

ARG SOURCE_COMMIT
ARG BUILD_VERSION
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT \
      org.opencontainers.image.version=$BUILD_VERSION \
      org.opencontainers.image.source=https://github.com/HazelKahlil/StockWatcher
ENV STOCKWATCHER_SOURCE_COMMIT=$SOURCE_COMMIT \
    STOCKWATCHER_BUILD_VERSION=$BUILD_VERSION

WORKDIR /app
COPY --from=builder --chown=stockwatcher:stockwatcher /app/.venv /app/.venv
COPY --from=builder --chown=stockwatcher:stockwatcher /app/src /app/src
COPY --chown=stockwatcher:stockwatcher pyproject.toml README.md ./

USER 10001:10001

EXPOSE 8000
CMD ["python", "-m", "stock_watcher.server.web"]
