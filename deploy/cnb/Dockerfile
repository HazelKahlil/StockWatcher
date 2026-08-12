# CNB only-preview runtime for the isolated StockWatcher Web line.
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

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    TZ=Asia/Shanghai

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources \
    && python -m pip uninstall --yes pip \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        ca-certificates docker.io fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 stockwatcher \
    && useradd --uid 10001 --gid stockwatcher --create-home \
        --home-dir /home/stockwatcher stockwatcher \
    && mkdir -p /app \
    && chown -R stockwatcher:stockwatcher /app /home/stockwatcher

WORKDIR /app
COPY --from=builder --chown=stockwatcher:stockwatcher /app/.venv /app/.venv
COPY --from=builder --chown=stockwatcher:stockwatcher /app/src /app/src
COPY --chown=stockwatcher:stockwatcher pyproject.toml README.md ./

USER 10001:10001

EXPOSE 8686
CMD ["python", "-m", "stock_watcher.server.web"]
