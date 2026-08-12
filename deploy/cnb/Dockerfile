# CNB only-preview runtime for the isolated StockWatcher Web line.
FROM docker:27.5.1-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c AS docker_cli

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

COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker

RUN python -m pip uninstall --yes pip \
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
