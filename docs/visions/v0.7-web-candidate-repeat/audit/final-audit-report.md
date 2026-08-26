# StockWatcher Web「近期多次出现」零回归终验报告

日期: 2026-08-26  
工作树: `/Users/kahlilhazel/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/web/source-worktree-87a8b85609f57504861e09f416694582556b736e`  
环境: macOS Docker Desktop + Cloudflare Tunnel（`stock.hazelkahlil.com`）  
GitHub: 未 push  
Web 状态: `BLOCKED / NOT_ACCEPTED`

审查包目录: `docs/visions/v0.7-web-candidate-repeat/audit/`

---

## 1. 审计基线

| 项 | 本机事实 |
| --- | --- |
| 当前分支 | `feat/web-candidate-repeat` |
| 当前 HEAD（文档提交 DOC） | `bf07c2284eba38c30dd5009e3d2aeb673dd71a3c` |
| FEATURE（实现提交） | `4b1e79e1250bc1bc8b4beff41f9f5bfbeb9b5997` |
| BASE（旧镜像 `SOURCE_COMMIT` / OCI revision） | `34ce825014692aef01ae397499dd7604c67273ef` |
| 旁路父提交（repeat 只含此区间） | `0c7b301a66406f62fbaecc64edb9509c18b59027` |
| 旧镜像 | `stockwatcher-web:web-alpha4-34ce825` |
| 旧镜像 digest | `sha256:e270d6601596e414d5e29595317f4d2557c22f702d4f434856990d4b3ce6986e` |
| 新镜像 | `stockwatcher-web:web-repeat-4b1e79e` |
| 新镜像 digest | `sha256:cbee6514797747197ba3744e03a3e46ddce6455956a9bd169191b55ad3a34c39` |
| 审计重建 tag | `stockwatcher-web:audit-rebuild-4b1e79e`（层缓存命中；manifest list `sha256:bdf6bd51…`，带 attestation，与 live 镜像 id 不同） |
| 当前 web/worker 镜像 | `stockwatcher-web:web-repeat-4b1e79e` / `sha256:cbee6514…` |
| web 启动 | `2026-08-26T04:10:35.927Z` |
| worker 启动 | `2026-08-26T04:45:10.007Z`（晚于 web，容器曾重启） |
| 数据库路径 | `/var/lib/stockwatcher/db/stockwatcher.db` |
| Live Schema | v10（`schema_version` 一行，值为 10） |
| Worker lease | `lease_name=stockwatcher-worker`，审计时 `worker_lease_held=true`，heartbeat 年龄 < 1s |
| 迁移前备份 | 主机 `~/StockWatcherBackups/auto-20260826T040404Z/stockwatcher-20260826T120405Z`；卷 `/backups/auto-20260826T040404Z/stockwatcher-20260826T120405Z` |
| 备份 SQLite SHA-256 | `ce5766a53604bcc3be3230013f17d0a47591ebcaa0f6263fe9788993ed62ed11` |
| 备份 Schema / 源提交 | v9 / `34ce825014692aef01ae397499dd7604c67273ef` |
| 本次只读 live 备份 | `/backups/audit-readonly-20260826T045900Z/stockwatcher-20260826T125912Z`；副本 SHA-256 `7e314760f05c4415f47e990fa9ad57b59da331bd09153cd2dce82fdbdb5e1625` |
| 回算摘要（live `app_settings.candidate_repeat_backfill_status`） | `status=completed version=1 snapshots=5278 occurrences=1618 activated=90 skipped=0` |
| 工作树相对交付报告 | 一致；另有未提交审查脚本与本目录。`git status` 在开工时干净，收尾时仅新增审计产物 |

`BASE` 是 `FEATURE` 的祖先。`34ce825..4b1e79e` 含 7 个提交：文档/隧道运维/安全修复 + 本功能。镜像名 `web-alpha4-34ce825` 的 OCI `revision` 与 `STOCKWATCHER_SOURCE_COMMIT` 均为完整 SHA `34ce825014692aef01ae397499dd7604c67273ef`。

---

## 2. 代码差异

完整清单: `changed-files.txt`（=`BASE..FEATURE`）。  
旁路增量清单: `changed-files-repeat.txt` 原件为 `changed-stat-repeat.txt` 对应的 `repeat-only.diff`（`0c7b301..FEATURE`）。

`git diff $BASE..$FEATURE -- src/stock_watcher/engine src/stock_watcher/providers` 为空文件（0 字节）。  
`CandidateEngine`、`StableTop3`、`AlertPolicy`、`StrongMovementDetector`、板块硬门、供应商归一化源码在该区间无 diff。

### 2.1 旁路增量（`0c7b301..4b1e79e`，24 文件）

| 文件 | 原因 | 类型 | 旧接口/旧数据/旧行为 |
| --- | --- | --- | --- |
| `src/stock_watcher/runtime/repeat_tracker.py` | 独立计数 sidecar | 新增 | 只写新表 |
| `src/stock_watcher/runtime/__init__.py` | 导出 tracker | 引用 | 无算法变化 |
| `src/stock_watcher/storage/sqlite.py` | Schema v10、history 可选 `repeat_active` 过滤 | 增量迁移 + 可选查询 | 旧列/旧主键保留；过滤默认关闭 |
| `src/stock_watcher/services/stockwatcher_service.py` | 健康快照事务内 observe；提醒 `note_source_in`；payload 附加 repeat 字段 | 接入 | 旧候选字段仍写入；附加键可剥离 |
| `src/stock_watcher/services/public_state.py` | 投影叠加 repeat 字段 | 字段增加 | 旧键保留 |
| `src/stock_watcher/server/api.py` | history 可选 `repeat_active`；历史点-in-time 字段 | 字段/查询增加 | 旧 history 项键保留 |
| `src/stock_watcher/server/static/dashboard.js` | 仅 `trigger_type==intraday` 弹窗徽标 | UI 增量 | `notify(` 仍为原有 2 处；`cardFor` 无徽标 |
| `src/stock_watcher/server/static/history.js` | 历史徽标 +「只看紫色标记」 | UI 增量 | 旧筛选参数保留 |
| `src/stock_watcher/server/static/app.css` | `.repeat-badge` | 样式增量 | 涨跌色规则仍在 |
| `src/stock_watcher/server/templates/{base,dashboard,history}.html` | 静态资源 cache bust；历史筛选项 | 模板增量 | 旧导航保留 |
| `tests/test_candidate_repeat_tracker.py` | 新功能测试 | 新增 | — |
| `tests/test_{candidate_outcomes,v02_core,v1_real_candidates,web_auth_api,web_services,web_ws}.py` | schema 断言 9→10；契约覆盖新字段 | 测试调整 | 旧断言仍在；见测试节 |
| 文档 / `CHANGELOG.md` / `storage.md` / `tracks/web.md` | 版本记录 | 文档 | — |

### 2.2 整包部署额外文件（`BASE..FEATURE`，安全/运维，非 repeat 算法）

`2160863` / `664c8c2`：`auth.py`、`security.py`、`web.py`、`ws.py`、`api.py` 错误处理、`dashboard.js`/`app.js` 合并逻辑、`health/ready` 改为 `asyncio.to_thread(_readiness_status)` 且 503/200 正文只保留 `status`。  
`f057124` / `0c7b301`：隧道、watchdog、backup 只读源。  
`deploy/**`、`.github/workflows/governance.yml`、`admin_cli.py`、`worker.py`、`command_service.py`、`storage/web.py`、`config.py`：运维与安全。

OpenAPI：生产环境 `openapi_url=None`。测试环境 `/api/v1/openapi.json` 中 `/api/v1/history` 查询参数为 `limit, cursor, from, to, code, repeat_active`（最后一项可选）。旧 history 项字段仍在响应中；候选对象增加 `repeat_*`。

允许差异: `repeat_*` 七字段、Schema v10 新表/索引、`app_settings.candidate_repeat_backfill_status`、history 可选过滤。  
禁止差异检查结果: 引擎/供应商 diff 为空；旧业务表行级缺失/改写为 0（见第 4 节）。

---

## 3. 测试

| 项 | 结果 |
| --- | --- |
| BASE 收集 nodeid | 540 |
| FEATURE 收集 nodeid | 591 |
| 旧测试缺失 | **0**（`tests-missing-nodeids.txt` 空） |
| 新增 nodeid | 51（15 个 repeat tracker + history 紫筛 + 安全修复测试） |
| `pyproject.toml` addopts | `BASE..FEATURE` diff 为空；仍为 `-q -m "not live_tushare"` |
| pytest（FEATURE，`-m 'not live_tushare' -ra -W error -o addopts=''`） | **564 passed, 25 skipped, 2 deselected, warnings 由 `-W error` 计为 0**；exit **0**；67.98s |
| deselected | 2（`live_tushare`） |
| skipped | 5 fixture parity + 20 Windows PowerShell |
| Ruff `uv run ruff check .` | exit **0** |
| Mypy `uv run mypy src tests` | exit **0**（145 files） |
| `python3 scripts/validate_workspace.py` | exit **0** |
| `uv run python scripts/check_windows_package.py` | exit **0** |
| `git diff --check` | exit **0** |
| `node --check` dashboard.js / history.js / app.js | 各 exit **0** |
| `uv sync --all-groups --frozen` | exit **0** |
| `uv lock --check` | exit **0** |
| Secret scan evidence | `status=PASS`，confirmed=0；`tests/test_web_auth_api.py` 行号 54/55 相对当前 fixture 行 55/56 **过期 1 行**（P2） |
| pip-audit 当前 venv `--skip-editable` | **No known vulnerabilities found**；exit **0** |
| pip-audit 对 export requirements | ensurepip SIGABRT，exit 1（工具故障，见 P2） |
| Docker build | exit **0**（缓存命中） |

旧测试改名/弱化/skip 增加: 未发现。repeat 测试为新增文件 + 追加用例。

---

## 4. 数据库

| 项 | 结果 |
| --- | --- |
| 迁移前 Schema | v9 |
| 迁移后 Schema（v9 副本 `initialize()` 两次） | v10 |
| 新表 | `candidate_repeat_days`（`UNIQUE(code, trade_date)`）、`candidate_repeat_states` |
| 新索引 | `idx_candidate_repeat_days_date`、`idx_candidate_repeat_days_active`、`idx_candidate_repeat_states_active` |
| sqlite_master 删除/重命名/改旧对象 SQL | 空 |
| 副本 integrity_check | ok |
| 副本 foreign_key_check | 0 行 |
| live 副本 integrity_check | ok |
| live 副本 foreign_key_check | 0 行 |
| OLD_ROWS_MISSING | **0** |
| OLD_ROWS_MUTATED | **0** |
| candidate_outcomes 身份字段 missing/mutated | **0 / 0**（66=66） |
| app_settings 旧键 | missing=0 mutated=0；唯一新增键 `candidate_repeat_backfill_status` |
| web_users | 6=6 |
| candidate_snapshots / items / alerts / daily_summaries | 计数与字节级旧主键完全一致（5278 / 15834 / 62 / 12） |
| runtime_sessions / web_sessions | 63→65 / 55→57（运行中增长，无批量清空） |
| web_events | 21332=21332 |

`notes` 与 `config_versions` 在备份与 live 中均为 0 行。

---

## 5. 回算

精确定义: **1618 条股票-交易日唯一记录**（`candidate_repeat_days` 行数），日历交易日 distinct=**13**。

过滤条件（代码 + 本审计 provenance 复算）:

1. `candidate_snapshots.health = HEALTHY`（persist 路径只写健康快照；库内 5278 行全部 HEALTHY）
2. `provider_is_countable`: provider_version 非空且 casefold 不含 `mock|replay|synthetic|demo|fixture`
3. `parse_shanghai_timestamp(source_ts)` 成功（tz-aware，转到 Asia/Shanghai）
4. 该快照 `candidate_items` 恰好 3 条（展示 Top3）
5. 代码匹配 `^[0-9]{6}\.(SH|SZ|BJ)$`

| 项 | 值 |
| --- | --- |
| snapshots_total / used | 5278 / 5278 |
| skipped | {} |
| provider_version | `tushare-1.4.29` × 5278 |
| config_version | `v1-real-candidates-20260729` × 5278 |
| app_version | `0.4.0a1`×1158，`0.6.0a4`×4120 |
| source_ts 范围 | 2026-08-10T10:39:57+08:00 → 2026-08-26T11:30:03+08:00 |
| tz | **全部 `+08:00`**（5278） |
| distinct 交易日 | 13（2026-08-10 … 2026-08-26） |
| distinct code | 1119 |
| 股票-交易日记录 | 1618 |
| 激活股票 | 90 |
| 非生产 provider 进入计数 | **0** |
| `(code, trade_date)` 重复组 | **0** |
| 激活样本 14 日/点-in-time 紫 | 90/90 failure_count=0 |
| 两次 backfill（live 副本） | `BACKFILL_IDEMPOTENT=PASS`；days/states/active 1618/1119/90 不变；旧业务表计数不变 |

问答:

1. HEALTHY 之外: countable provider、tz-aware source_ts、恰好 3 条 items、合法代码。  
2. Replay/Mock/Synthetic: provider_version 子串拒绝；本库 provider 全为 `tushare-1.4.29`。  
3. 测试库: 生产卷备份；config/app 为 v1-real / 0.4.0a1|0.6.0a4。  
4. source_ts: 5278 条均带 `+08:00`；`parse_shanghai_timestamp` 再 `astimezone(Asia/Shanghai)`。  
5. 计数对象: `record_batch_in` 只写展示 Top3 到 `candidate_items`；回算取 rank 前 3。  
6. 缺失/错误时间: naive 或无法 parse 则 skip；本库 skip=0。  
7. 同股同日: `UNIQUE(code, trade_date)` + `_touch_day` 只合并 `source_types`。

---

## 6. 非回归

脚本: `scripts/verify_repeat_non_regression.py`  
输出: `non-regression.json` → **`NON_REGRESSION=PASS`**

同一 fixtures / 时钟 / CandidateEngine / AlertPolicy / StrongMovementDetector / DailySummaryEngine:

- 最终候选、分数、排名、强/中/近、正式/补位、Stable Top3 顺序: core_diff_count=**0**
- 09:45 / 14:45 / 盘中强异动 / 冷却 / 每日上限: core 一致
- FEATURE persist 后剥离 `repeat_*` 的 public_state 相对 BASE: 仅 `updated_at` 差约 0.4s（SQLite `datetime('now')`），其余一致

隔离 TestClient（live v10 副本，测试用户）:

- `/api/v1/state` 旧键存在，并附加 `repeat_*`
- `/api/v1/history` 旧项键完整；候选附加 `repeat_*`；`repeat_active=true` 可过滤
- `/api/v1/alerts` 旧键，无顶层 `repeat_*`
- `/api/v1/outcomes` 200，records=66
- 首页 `cardFor` 无 `repeat-badge`；历史页有「只看紫色标记」；alerts/outcomes/summary/admin 无紫筛
- `dashboard.js` `notify(` **2**（与 BASE 相同两处：盘中强异动 + 固定提醒）；无 `Notification(` / `new Audio`

旧镜像回滚栈 history 候选 **无** `repeat_*`，证明旧契约可从 v9 备份完整读出。

---

## 7. 线上验证

| 项 | 结果 |
| --- | --- |
| 容器镜像 | web/worker = `web-repeat-4b1e79e` / `sha256:cbee6514…` |
| Worker 模块 healthcheck | `ready`，lease held，exit 0 |
| Web 模块 healthcheck（schema） | `web ready`，exit 0 |
| Docker web health / 公网 `/health/ready` | **unhealthy / HTTP 503 `{"status":"not_ready"}`**（见问题） |
| `/health/live` | HTTP 200 |
| 公网登录页 | HTTP 200；无紫色 markup；`app.css?v=15` |
| 静态 | `app.css?v=15`、`dashboard.js?v=12`、`history.js?v=2` 均为 200 |
| 旧历史/提醒/复盘/总结（副本 API） | 均可读；条数见上 |
| 旧用户 | 6，无需重建 |
| 真实强异动弹窗 | 代码验收走隔离 fixture；现场观察仍待下一交易日 |

未在报告中写入 Cookie、Token、master key、密码或完整用户表。

---

## 8. 回滚演练

见 `rollback-smoke.txt`。**`ROLLBACK_RESTORE=PASS`**

独立容器 `sw-audit-rollback`，`127.0.0.1:18080`，`STOCKWATCHER_ENV=test`，v9 备份副本，旧镜像 `web-alpha4-34ce825`。无生产 Tunnel。演练后已删除容器。

`/health/ready` 在该拓扑为 503（备份中的 worker lease 已过期，且未启动 worker）。`/health/live`、登录、state/history/alerts/outcomes、历史/复盘/总结页均为 200。旧镜像能用该备份启动并读取全部旧业务数据。

---

## 9. 问题分级

| 级 | 数量 | 说明 |
| --- | --- | --- |
| P0 | **0** | 旧主键无缺失、无改写；引擎 diff 空；无密钥进入报告 |
| P1 | **1** | 现网 Web `/health/ready` 持续 503，Compose healthcheck 将 web 标为 unhealthy（failing_streak>100）。同一时刻容器内 `worker_readiness()` 为 True、Worker 模块检查 ready、`/health/live` 200、登录页 200。处理函数把任意异常压成 `{"status":"not_ready"}`（提交 `2160863`，位于 `BASE..FEATURE`，repeat 提交未改该函数）。按第十五节 Docker healthcheck 门，**整包不能签收**。未改现网、未重迁库。 |
| P2 | **3** | (a) `/health/ready` 200 正文相对 34ce825 去掉 `schema_version` / worker 诊断字段（同一安全提交，未认证端点缩小暴露）；(b) `evidence/security/repo-secret-scan.json` 对 `test_web_auth_api.py` 行号落后 1 行；(c) 对 `uv export` 需求文件跑 pip-audit 因本机 ensurepip SIGABRT 失败，已用当前 venv `--skip-editable` 得到 0 漏洞 |

已修复项: 无（按规则不在 live 上热修）。  
未完成项: 现网 `/health/ready` 503 根因需在副本打开异常日志后另开修复提交；真实交易日强异动弹窗现场观察；secret scan 行号；GitHub 未同步。

---

## 10. 最终结论

旁路增量（`0c7b301..4b1e79e`）下，评分/排名/Stable Top3/提醒决策与旧历史行级数据兼容性验证通过，可继续把该功能留在 Web 内部试用栈。

现网 Docker web healthcheck 门未过（`/health/ready` 503）。本次 **不能** 按第十五节全部签收门宣布整包零回归通过。

Web 继续 **`BLOCKED / NOT_ACCEPTED`**。

---

## 审查包文件

| 文件 | 内容 |
| --- | --- |
| `final-audit-report.md` | 本报告 |
| `changed-files.txt` | `BASE..FEATURE` 名称状态 |
| `repeat-feature.diff` | `BASE..FEATURE` 完整 diff |
| `repeat-only.diff` | `0c7b301..FEATURE` |
| `test-results.txt` | pytest 全文 |
| `non-regression.json` | `NON_REGRESSION=PASS` |
| `old-data-compatibility.json` | `OLD_ROWS_MISSING=0` `OLD_ROWS_MUTATED=0` |
| `backfill-provenance.json` | 过滤与来源分布 |
| `migration-results.txt` | v9→v10 副本迁移 |
| `rollback-smoke.txt` | `ROLLBACK_RESTORE=PASS` |
| `snapshots/public-login.png` | 公网登录页 |
| `snapshots/history-repeat.png` | 历史「只看紫色标记」渲染效果（live v10 副本真实数据 + 本仓库 CSS/JS 逻辑离线渲染） |
| `snapshots/overlay-intraday.png` / `overlay-intraday-closeup.png` | 盘中强异动弹窗渲染效果（同上，紫色徽标示例 `603118.SH 近14天第3次`） |
| `snapshots/preview/*.html` | 上述渲染截图的脱敏静态源页 |
| `snapshots/*.html` | 隔离栈脱敏 DOM |

未收录: 数据库文件、`.env.tunnel`、master key、Cookie、真实运行日志。
