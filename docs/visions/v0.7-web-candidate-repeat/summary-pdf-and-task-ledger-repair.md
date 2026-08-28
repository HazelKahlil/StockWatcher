# Web 盘后 PDF 与任务账本返修

> 状态: 已部署到 Mac 内测隧道；Web 继续 `BLOCKED / NOT_ACCEPTED`
> 任务锚点: 盘后总结 PDF、固定提醒任务账本、过期任务重标
> 创建: 2026-08-28

## 节点

| 项 | 值 |
| --- | --- |
| 起始 HEAD | `a15aa241e8b5ef352e16753b38152fcf758932a3`（`fix/web-readiness-503`） |
| 修复提交 | `d84c3c77a0e33be9790697c969f311dff5e9ea8c` |
| 文档提交 | 同目录 docs commit（本文件） |
| 分支 | `fix/web-summary-pdf-and-task-ledger` |
| 新镜像 | `stockwatcher-web:web-summary-fix-d84c3c7` |
| 镜像 digest | `sha256:4e836255f292d9d8a673d8f2aa120c9240a7b5943d6091252d29717118bb12a2` |
| SOURCE_COMMIT | `d84c3c77a0e33be9790697c969f311dff5e9ea8c` |
| BUILD_VERSION | `web-summary-fix-d84c3c7` |
| 回退镜像 | Web `web-repeat-ready-db10869` / Worker `web-repeat-4b1e79e` |
| GitHub | 未 push |

## 根因

1. **容器内盘后 PDF**：`post_close_pdf._register_fonts()` 只探测 macOS STHeiti 与 Windows 微软雅黑。Linux 走 CID fallback，把模块全局 `_FONT` 改成 `STSong-Light`；`local_summary_pdf` 仍使用冻结常量 `StockWatcherSansMedium`。reportlab 抛 `ValueError: Can't map determine family/bold/italic for stockwatchersansmedium`。自 8/20 起 worker 每天 400+ 次重试失败。
2. **固定提醒账本**：14:45 提醒已在跨界 automatic 扫描发出后，下一 tick 认领任务时本轮覆盖率失败。`_evaluate_alerts` 在「今日已有该触发提醒」检查之前因 `HealthState` 非 HEALTHY 返回 None，任务被标 failed。义务是提醒送达。
3. **过期重标**：`_expire_automation_tasks` 对已是 FAILED 的过期任务每个 tick 再 `_mark_task(FAILED)`，`updated_at` 被刷新。

## 改动

- `_register_fonts()` 增加 Linux 候选：`/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` 与 `NotoSansCJK-Bold.ttc`，`subfontIndex=2`（TTC name table：Regular/Bold 第 2 面均为 Noto Sans CJK SC）。
- `registered_font_names()` 先注册再返回实际生效的 `(_FONT, _FONT_MEDIUM)`；`local_summary_pdf` 的样式与页脚全部改用该返回值。
- `generate_summary()` 各 `except Exception` 写 `logger.warning(..., exc_info=True)`，带 `stage=`，重试语义不变。
- `_evaluate_alerts` 把「今日已有该 trigger」检查移到健康门之前。
- `_tick_locked`：tick 结束时该触发今日提醒已存在则任务 SUCCEEDED（有 snapshot_id 带上；没有则 detail「提醒已在跨界扫描中发出。」）；无提醒且本轮扫描失败仍 FAILED。
- `_expire_automation_tasks`：state 已是 FAILED 则 continue。

未改提醒策略、排名、评分；未改 `src/stock_watcher/ui/tushare_v1_session.py`；未重启 cloudflared / tunnel-gateway；未删卷。

## Noto SC 探测

Worker 容器 `stockwatcher-worker-1`（当时镜像 `web-repeat-4b1e79e`）上，reportlab `TTFont(..., subfontIndex=i)` 对 Regular/Bold 两个 ttc 的 index 0–7 全部抛 `TTFError: postscript outlines are not supported`（sfnt tag `OTTO` / CFF）。

用 stdlib 读 TTC name table：

| 文件 | numFonts | SC 面 |
| --- | --- | --- |
| `NotoSansCJK-Regular.ttc` | 10 | index **2** `Noto Sans CJK SC` Regular |
| `NotoSansCJK-Bold.ttc` | 10 | index **2** `Noto Sans CJK SC` Bold |

index 0/1/3/4 为 JP/KR/TC/HK；5–9 为对应 Mono。

reportlab 4.5.1 无法把该 CFF 集合登记为 `TTFont`。Linux 在 Noto 候选失败后走 CID `STSong-Light`。跨模块字体名对齐后，这条 fallback 可以产出 PDF。

## 测试与门禁

- `test_local_fallback_pdf_renders_when_all_ttf_candidates_missing`：强制全部 TTF 候选 `is_file=False`，`render_local_fallback_pdf` 产出 `%PDF`，注册名为 `STSong-Light`
- `test_fixed_task_succeeds_when_today_alert_exists_despite_failed_scan`
- `test_fixed_task_fails_when_scan_fails_and_today_alert_is_missing`
- `test_expired_failed_task_is_not_remarked_on_later_tick`
- `test_summary_artifact_write_failure_logs_warning_and_retries`

| 门禁 | 结果 |
| --- | --- |
| `uv run pytest` | 580 passed, 25 skipped, 2 deselected |
| `uv run ruff check .` | All checks passed |
| `uv run mypy src tests` | Success: no issues found in 145 source files |
| `git diff --check` | 通过（exit 0） |

## 部署证据（macOS Docker Desktop + Cloudflare Tunnel）

compose 在 `deploy/`：`docker compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file .env.tunnel`。只 `up -d --no-deps web worker`。

| 项 | 证据 |
| --- | --- |
| `/health/ready` | `curl -m 5 http://127.0.0.1:18000/health/ready` → 200 `{"status":"ready"}` |
| 容器镜像 | web / worker 均为 `stockwatcher-web:web-summary-fix-d84c3c7`（`sha256:4e8362…12a2`），healthy |
| tunnel | `stockwatcher-cloudflared-1` created 2026-08-18、started 2026-08-27；`stockwatcher-tunnel-gateway-1` 同，未重建 |
| PDF 复现 | worker 内 `SQLiteStore(..., read_only=True)` + `write_local_fallback_artifacts(..., reports_dir=/tmp/summary-repro)` 成功；`registered_fonts ('STSong-Light', 'STSong-Light')`；`/BaseFont` 含 `STSong-Light` |
| `summary-15:30` | `2026-08-28:summary-15:30` → `succeeded` at `2026-08-28T19:17:31.428178+08:00`（worker 19:17:24 启动后约 7 秒） |
| 总结文件 | `/var/lib/stockwatcher/db/reports/2026-08-28-A股盘后回顾.pdf` 8002 bytes，manifest `source_commit=d84c3c77…`，同目录 json/md/meta 齐 |
| 过期任务 | `scheduled-09:45` / `scheduled-14:45` 保持 `failed`；`updated_at` 两次查询均为 `2026-08-28T19:17:00.772279+08:00`（19:19:02 与 19:20:49）。14:45 不回填翻绿；09:45 保留早上 Docker 卡死失败证据 |

## 边界与欠账

- 桌面线 `src/stock_watcher/ui/tushare_v1_session.py` 仍有过期任务重复标 failed、以及 `generate_summary` 静默 `except Exception`。本次未动。
- Debian `fonts-noto-cjk` TTC 为 CFF，reportlab TTFont 不能嵌入 Noto SC。当前 Linux 盘后 PDF 字体为 CID `STSong-Light`。
- Worker `secret-prune` FOREIGN KEY 告警仍在，与本次无关。
- Web 继续 `BLOCKED / NOT_ACCEPTED`。
