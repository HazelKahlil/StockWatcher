# v0.7-web-candidate-repeat：Web「近期多次出现」紫色观察提示

> 状态: 进行中
> 交付层级: 本地实现与离线验证已完成；受控部署 / 公网页面验证待执行
> 任务锚点: Web 独立线 `feat/web-candidate-repeat`
> 创建: 2026-08-26 ｜ 封版:

## 目标与范围

为 Web 端最终展示的稳定 Top3 增加独立「近期多次出现」识别：同一股票在连续
14 个自然日内覆盖三个不同交易日后永久激活紫色标识，之后按不同交易日累计次数。

本功能只做观察提示。模型评分、候选排名、Stable Top3、「强 / 中 / 近」和盘中强异动
判断继续走原路径。它也不构成买入、卖出或收益预测。

客户确认来源：`StockWatcher2.docx` 问卷；工程规则见本目录交付 brief。

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
- [x] 数据库迁移、完整性检查和回滚备份通过（v9→v10 前置 `.pre-v10.bak` + `integrity_check`）
- [ ] Docker 构建、受控部署、公网页面验证

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-08-26 | 登记版本并实现 Web Schema v10 / CandidateRepeatTracker / API / 弹窗与历史 UI | 本地完成 |
| 2026-08-26 | `uv run pytest`、`ruff check .`、`mypy src tests`、`python3 scripts/validate_workspace.py` 通过 | 本地完成 |
| 2026-08-26 | Docker 构建、live 备份、Schema v10 受控部署与公网页面验证 | 待执行 |

## 实现要点

- 独立 sidecar：`src/stock_watcher/runtime/repeat_tracker.py`
- Schema v10 表：`candidate_repeat_days`（`UNIQUE(code, trade_date)`）、`candidate_repeat_states`
- 写入时机：健康候选快照与明细同一事务内追踪；提醒只 `note_source_in` 合并当天来源
- 历史页使用 `active_after` / `count_after` / `span_days_after` 的当时状态
- 当前 REST / `candidates.updated` / `alert.created` 带 repeat 字段；强异动弹窗优先用该提醒 payload 中的候选
- Worker 首次 tick 对已有健康快照做幂等回算，结果写入 `app_settings.candidate_repeat_backfill_status`

## 生命周期三账对账

| 检查点 | 任务载体 | `docs/visions/README.md` | 本 README | 结果 |
| --- | --- | --- | --- | --- |
| 开工 | `feat/web-candidate-repeat` / in_progress | 活跃登记 v0.7-web-candidate-repeat | 范围已写入 | 一致 |
| 重要集成 | 本地 pytest / ruff / mypy 已通过 | 待部署后更新 | 本表已更新 | 部分 |
| 封版 | 待完成 | 待完成 | 待完成 | 待核 |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| 滚动 14 日窗口 | 客户未定义首窗未达标处理；用仍有效日期作为新起点，避免长期漏判 |
| 计数发生在健康快照写入事务 | 提醒只补充当天来源，不能反向驱动次数 |
| Web 继续 `BLOCKED / NOT_ACCEPTED` | 本功能上线后仍为内部测试，不宣称生产稳定 |
| live Schema v10 | 仅新增两张表；部署前必须走现有 backup → migrate → healthcheck，回滚用该次备份 |

## Session Handoff 索引

<!-- 本版落盘的交接文件在此登记 -->

## 封版记录

- 验证结果：
- 遗留问题：受控部署与公网页面验证尚未执行
- 三账终态：
- 同步债 / successor：
