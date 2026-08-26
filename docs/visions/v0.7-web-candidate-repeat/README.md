# v0.7-web-candidate-repeat：Web「近期多次出现」紫色观察提示

> 状态: 本地实现已部署到 Mac 内测隧道；Web 继续 `BLOCKED / NOT_ACCEPTED`
> 交付层级: 代码、迁移、回算、测试、Docker 构建、受控部署与公网静态验证已完成
> 任务锚点: Web 独立线 `feat/web-candidate-repeat`
> 创建: 2026-08-26 ｜ 封版:

## 目标与范围

为 Web 端最终展示的稳定 Top3 增加独立「近期多次出现」识别：同一股票在连续
14 个自然日内覆盖三个不同交易日后永久激活紫色标识，之后按不同交易日累计次数。

本功能只做观察提示。模型评分、候选排名、Stable Top3、「强 / 中 / 近」和盘中强异动
判断继续走原路径。它也不构成买入、卖出或收益预测。

客户确认来源：`StockWatcher2.docx` 问卷。

明确不在本版：

- 首页实时候选卡紫色
- 09:45 / 14:45 固定提醒弹窗紫色
- 股票详情页紫色
- 新增弹窗、声音、浏览器通知或手机通知
- 紫色置顶、模型加分
- 修改 CandidateEngine / Stable Top3 / 强异动算法
- 扩展到 Mac 或 Windows 客户端

## 验收标准（必须可检查）

- [x] 同股同日扫描 100 次只计 1 次
- [x] 同日实时、固定提醒、强异动重合只计 1 次
- [x] 同日离开再进入仍只计 1 次
- [x] 三次都在同一天不激活
- [x] 三个不同交易日在 14 个自然日内，第三次激活
- [x] 第三次发生在第 14 天，激活
- [x] 第三次发生在第 15 天，最早过期日期不参与
- [x] 旧日期过期后仍有效日期不会被全部清空
- [x] 激活后永久保留
- [x] 激活后新交易日出现，次数 +1
- [x] 激活后同日重复不增加次数
- [x] Worker 重启后状态不丢失
- [x] 浏览器刷新、重连、多用户登录不增加次数（计数在健康快照事务内，与会话无关）
- [x] 历史回算执行两次，结果一致
- [x] 历史第一次、第二次不紫，第三次开始紫色
- [x] 只有盘中强异动弹窗显示紫色（首页 `cardFor` 不含徽标；`showRepeat` 仅 `intraday`）
- [x] 固定提醒弹窗和首页不显示紫色
- [x] 没有新增重复出现专属声音、通知或弹窗（`dashboard.js` 仍只有原有两处 `notify(`）
- [x] 模型分数、排名、Stable Top3 和强异动逻辑不变（`engine/{candidates,stable_top3,alerts}.py` 未引入 repeat）
- [x] 数据库迁移、完整性检查和回滚备份通过（v9→v10 前置备份 + live `integrity_check=ok`）
- [x] Docker 构建、受控部署、公网静态页面验证

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-08-26 | Schema v10 / CandidateRepeatTracker / API / 弹窗与历史 UI | 本地完成 |
| 2026-08-26 | `uv run pytest`、`ruff check .`、`mypy src tests`、`python3 scripts/validate_workspace.py` | 通过 |
| 2026-08-26 | 备份 Schema v9 → 镜像 `stockwatcher-web:web-repeat-4b1e79e` → live 迁到 v10 → 公网静态验证 | 完成 |
| 2026-08-26 | 零回归终验包 `docs/visions/v0.7-web-candidate-repeat/audit/`；旁路数据/算法门通过；现网 `/health/ready` 503 使整包签收门未齐 | 记录 |
| 2026-08-26 | 独立分支 `fix/web-readiness-503`：公开 503 仍为 `{"status":"not_ready"}`，服务器记录分阶段脱敏日志；HTTP readiness 使用短生命周期只读连接 | 进行中 |

## 实现要点

- 独立 sidecar：`src/stock_watcher/runtime/repeat_tracker.py`
- Schema v10 表：`candidate_repeat_days`（`UNIQUE(code, trade_date)`）、`candidate_repeat_states`
- 写入时机：健康候选快照与明细同一事务内追踪；提醒只 `note_source_in` 合并当天来源
- 历史页使用 `active_after` / `count_after` / `span_days_after` 的当时状态
- 当前 REST / `candidates.updated` / `alert.created` 带 repeat 字段；强异动弹窗优先用该提醒 payload 中的候选
- Worker 首次 tick 对已有健康快照做幂等回算，结果写入 `app_settings.candidate_repeat_backfill_status`

## 交付节点

| 项 | 值 |
| --- | --- |
| 本地分支 | `feat/web-candidate-repeat` |
| 实现提交 | `4b1e79e1250bc1bc8b4beff41f9f5bfbeb9b5997` |
| 镜像标签 | `stockwatcher-web:web-repeat-4b1e79e` |
| 镜像 Id | `sha256:cbee6514797747197ba3744e03a3e46ddce6455956a9bd169191b55ad3a34c39` |
| 部署环境 | macOS Docker Desktop + Cloudflare Tunnel，`stock.hazelkahlil.com` |
| 迁移前备份 | `~/StockWatcherBackups/auto-20260826T040404Z`（Schema v9 / `34ce825`） |
| 容器内备份 | `/backups/auto-20260826T040404Z/stockwatcher-20260826T120405Z` |
| live Schema | v10，`integrity_check=ok` |
| 回算 | `status=completed version=1 snapshots=5278 occurrences=1618 activated=90 skipped=0` |
| 公网验证 | `/health/ready`、`app.css?v=15`、`dashboard.js?v=12`、`history.js?v=2`；未登录首页无紫色 markup |
| GitHub | 未 push；远端未包含本提交 |

## 回滚

1. 停止 `web` / `worker`。
2. `python -m stock_watcher.server.admin_cli restore --input /backups/auto-20260826T040404Z/stockwatcher-20260826T120405Z`
3. 将 `.env.tunnel` 的 `IMAGE_TAG` / `BUILD_VERSION` 改回 `web-alpha4-34ce825`，`SOURCE_COMMIT` 改回 `34ce825014692aef01ae397499dd7604c67273ef`。
4. `docker compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file .env.tunnel up -d web worker tunnel-gateway cloudflared`
5. 跑 `deploy/scripts/tunnel-healthcheck.sh`。

## 生命周期三账对账

| 检查点 | 任务载体 | `docs/visions/README.md` | 本 README | 结果 |
| --- | --- | --- | --- | --- |
| 开工 | `feat/web-candidate-repeat` | 活跃登记 v0.7-web-candidate-repeat | 范围已写入 | 一致 |
| 重要集成 | 本地验证 + Mac 内测部署 | 见本表 | 交付节点已写 | 一致 |
| 封版 | Web 仍为内部测试 | 待封 | 待封 | Web 继续 `BLOCKED / NOT_ACCEPTED` |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| 滚动 14 日窗口 | 客户未定义首窗未达标处理；用仍有效日期作为新起点，避免长期漏判 |
| 计数发生在健康快照写入事务 | 提醒只补充当天来源，不能反向驱动次数 |
| Web 继续 `BLOCKED / NOT_ACCEPTED` | 本功能上线后仍为内部测试，不宣称生产稳定 |
| Worker `secret-prune` FOREIGN KEY 告警 | 启动日志可见，与重复出现表无关；未在本版处理 |

## Session Handoff 索引

<!-- 本版落盘的交接文件在此登记 -->

## 封版记录

- 验证结果：离线工程门通过；Mac 内测隧道已部署 Schema v10。等待真实交易日看盘中强异动弹窗与历史紫色。
- 遗留问题：GitHub 未同步；真实交易日页面交互验收未做；Worker `secret-prune` 外键告警仍在。
- 三账终态：实施完成，版本未封。
- 同步债 / successor：需要时从本分支出 `publish/` 同步 PR。
