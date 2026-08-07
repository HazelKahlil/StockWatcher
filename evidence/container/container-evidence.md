# 容器验收证据（2026-08-07，macOS Docker Desktop，linux/arm64 镜像）

## 镜像

- `stockwatcher-web:web-v0.1.0-rc1`
- digest: sha256:8e73835e8de98548304c1deaf58fe8a923799cfd4567502764c5ba121ab25486
- 基础镜像: python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
- caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648
- labels: org.opencontainers.image.revision=<final commit>，version=web-v0.1.0-rc1

## 运行证据

- web 容器：/health/live=ok；/health/ready={status:ready, schema_version:7, worker_lease_held:true}
- worker healthcheck：{"status": "ready", "heartbeat_age_seconds": <5}
- 双 worker（WRK-001）：第二个实例日志
  "safe exit without scanning: lease stockwatcher-worker held by <holder>"，exit=0；
  lease holder 与 fencing_token 未被改变
- HTTP 全流程：login 200、/me CSRF、/state（worker heartbeat、3 项自动任务）、
  manual-refresh 202 + coalesced、admin diagnostics（schema 7、lease、events）、
  scan-runs 86 条、users 列表
- 命令语义：manual_refresh 60s 后 failed/timeout（无 Token 时不越健康门）；
  token_test 失败 static_pro（占位 Token 分层探测被真实供应商拒绝，未激活）；
  token_update 先测后激活流程运行、响应不包含 Token 明文
- 备份演练：admin_cli backup 产出 SHA256SUMS.txt + manifest.json +
  stockwatcher.sqlite3 + reports/，schema 7
- 浏览器 E2E 13/13（见 evidence/browser-e2e/）

## 边界声明

- 本证据在 macOS Docker Desktop 上取得，仅证明镜像/进程/接口行为；
  不能充当 VPS Linux 原生文件系统与真实数据源的验收（见 LIVE-STATUS.md）。
- 容器存储层曾触发 SQLite WAL 并发损坏，根因与修复见 evidence/concurrency/；
  修复后容器内 90s 并发压测 integrity ok。
