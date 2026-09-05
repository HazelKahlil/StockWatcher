# StockWatcher 日常使用与恢复链路审核

日期：2026-09-05 · codex。结论层级：源码审核与 macOS 临时测试库复现；尚未实施修复。

## 使用前提与审核范围

Kahlil 明确确认周末不用系统，并会主动关闭 Docker。周末关机属于正常使用方式；不要求全天候在线，不应仅凭主动停机附近的 uptime、ready 或缺少新快照判定工作日服务故障。本次关注工作日可用、周末可停、重新启动后使用正确的数据。

权威项目 `20-Projects/StockWatcher` 的本地 main 为 `6a81825`，工作区干净。日常 Web 的独立 worktree 位于 `90-Archive/StockWatcher/00-current/web/source-worktree-87a8b85609f57504861e09f416694582556b736e`，审核基线为 `fix/web-summary-pdf-and-task-ledger@2d6589d`，包含会话开始时已有的 healthcheck 与 Compose 未提交改动。本文代码定位均相对于该 Web worktree。

本次未启动、重启或重建 Docker，未请求真实行情，未读取凭据，未改业务数据、排名规则或产品代码。未访问 GitHub，未合 main、未 push。Web 继续 `BLOCKED / NOT_ACCEPTED`，不扩展为 Windows 验收。

上轮还需纠正一项判断：`web_public_state` 为空不等于首页必为空。`PublicStateBuilder.build()` 在 `services/public_state.py:81–94` 会回退读取最近不可变快照，已有对应测试；在未完成登录页面验收时不能直接断言页面为空。

## 已确认的审核问题

### R1 · P1：运维入口遗漏现网存储配置

定位：`deploy/scripts/tunnel-up.sh:22`、`tunnel-down.sh:15`、`scheduled-backup.sh:67`；`deploy/scripts/db-preflight.sh:8–19`；`deploy/docker-compose.bind-db.yml:5–18`。

现网配置需要基础、tunnel、bind-db 三份 Compose。上述脚本只加载前两份；预检还直接指定旧 named volume。按这两份配置运行 `up`，会要求使用 `stockwatcher_tunnel_db`，而现网第三份覆盖后使用的是 `./state/db`。Worker 的 healthcheck 文件 bind 也会随之消失。停止脚本随后执行的临时 checkpoint 容器，以及 Web 不运行时的定时备份临时容器，同样可能使用另一套库。

离线解析结果：两文件的 DB source 为 `stockwatcher_tunnel_db`，三文件为 `./state/db`；只有三文件配置包含 healthcheck bind。未运行任何 Docker 命令。

建议：统一一个 Compose 入口供启动、停止、预检、备份共用；使用同一份明确的 DB、report、backup 与镜像配置。将已验证的轻量 healthcheck 固化进镜像，消除临时文件 bind。更新 main 的部署索引时只同步事实，不把 Web 实现合入 main。

验收：冷启动、热启动、停止后备份均解析到同一库；有旧 named volume 时也不会误选；healthcheck 来自新镜像；启动前后最近交易日、快照和报告数量一致。

### R2 · P1：预检自动移走 WAL 后可能带着旧数据放行

定位：`deploy/scripts/db-preflight.sh:97–121`。

任一预检失败后，脚本无条件隔离 WAL/SHM，再检查主库；主库通过就 exit 0。WAL 可以包含已提交但未 checkpoint 的事务，因而主库结构完好不足以证明最新业务数据保留。脚本保留了隔离文件，但当前可见数据仍可能回退。

临时库复现：子进程建立 WAL 库、关闭自动 checkpoint、提交一条记录后模拟非正常退出；按脚本逻辑隔离 WAL/SHM。隔离前记录数 1，隔离后记录数 0，隔离后 `quick_check=ok`。该实验仅证明隔离动作的后果，未声称复现了此前现网损坏原因。

建议：普通预检失败时停止自动修复动作，保全 DB/WAL/SHM 整组文件；在隔离副本中验证恢复路径，核对业务时间与数量，再由明确的恢复操作安装。不能用移走 WAL 后的结构检查代替业务完整性验证。

验收：在 WAL 含已提交新数据、瞬时锁冲突、WAL 真损坏三种夹具下，预检都不会静默放行数据回退；整组原文件与恢复来源可追溯。

### R3 · P1：恢复候选按 mtime 排序，未比较业务新鲜度

定位：`src/stock_watcher/storage/sqlite.py:280–309、359–400`；`src/stock_watcher/server/admin_cli.py:194–204`。

恢复会按文件 mtime 倒序，采用第一个通过 integrity/FK 检查的备份。当前备份 manifest 记录 schema、source commit 和文件哈希，但缺少最近候选时间、关键表数量与业务日期。旧数据文件被重新复制或修改时间后，可能排到更完整的备份前。

临时库复现：同源有效备份分别含 08-31 快照 ID 10、09-04 快照 ID 20；让旧数据文件 mtime 更大。现有恢复逻辑选择 ID 10，虽然 ID 20 的有效备份同时存在。所有文件均为合成夹具。

建议：备份 manifest 增加数据库来源标识、最近快照业务时间、固定时点任务、结算及报告计数；从同一来源的合格备份中依据业务水位选择。恢复后明确显示恢复日期和数据缺口。仅剩旧备份时进入明确的恢复决策，不能把旧数据包装为当前运行结果。

验收：颠倒 mtime 不改变业务上最新备份的选择；重启后能核对恢复来源；没有足够新的备份时给出明确的回退范围。

### R4 · P1：报告生成目录与备份目录不一致

定位：`src/stock_watcher/server/worker.py:48–51、84–96`；`src/stock_watcher/services/stockwatcher_service.py:1651–1652`；`src/stock_watcher/paths.py:44–48`；`src/stock_watcher/server/admin_cli.py:183–185`。

Worker 创建了 `ServerSettings.report_dir`，但构造业务服务时未传入该值。服务默认向数据库旁的 `db/reports` 写报告；备份 CLI 只复制 settings 指定的独立 reports 目录。数据库备份可成功而 PDF/JSON/Markdown 被遗漏。

临时目录复现：在服务实际报告目录创建合成 PDF 标记文件，执行真实 `cmd_backup`。返回码 0，数据库文件存在，报告文件未进入备份。

建议：Worker、服务、下载接口及备份统一使用显式 report_dir；补一条从 Worker 报告生成、备份到恢复读取的完整测试，校验同一报告文件与 manifest。

验收：同一天的 PDF/JSON/Markdown 全部进入备份，恢复到新临时目录后仍可读取，路径配置改变时测试可发现遗漏。

### R5 · P2：Web 交易时段兜底绕过交易日历

定位：`src/stock_watcher/services/stockwatcher_service.py:754–760`；对照 `src/stock_watcher/runtime/market_session.py:16–19`。

日历判断返回 false 后，Web 仍按 09:30–11:30 / 13:00–15:00 的钟点直接放行。因此 Docker 恰好在周末运行时，仍会尝试扫描。该问题也涉及日历明确关闭的工作日，不能只补一个 weekday 判断便覆盖全部情况。

纯函数复现：日历只包含 09-04、09-07；09-05 和 09-06 10:00 的日历判断为 false，Web 判断却为 true。未调用供应商。

建议：统一桌面和 Web 的交易日与时段决策，区分“已确认休市”和“日历不可用”；休市时显示最近交易日和下次预计运行时间。保留用户周末主动关 Docker 的使用方式。

验收：周末、明确节假日、午休不发起扫描；交易日恢复时继续正常调度；日历缺失按已有授权的降级规则处理，不能把已知休市改判为开市。

## 产品与性能优化建议

这些是后续改进项，不应与已确认的 P1 缺陷混为一谈。

1. **待结算有明确出口。** `_historical_settlement_is_due()` 在 attempts 达到 5 后永不自动放行，即使只修改 next_retry_at 也不会恢复；现有测试刻意保护该上限。保留有界重试，增加“尚未到期 / 自动重试中 / 重试已耗尽 / 数据不可验证”的清晰状态，以及 Admin 可审计、可预览的受控重试入口。仍按真实下一交易日同槽分钟数据结算，不引入日线替代或无限重试。
2. **胜率与样本完整度一起看。** 现有统计正确地只计算已结算样本；日组合必须上午三笔、下午三笔共六笔均已结算才计入。保留这些边界。页面可进一步显示完整组合天数、缺口数量、数据截至日期和异常原因，让用户理解尚未结算的数据覆盖情况。“个人胜率”标题可改得更贴近实际的候选理论复盘口径。
3. **给最近快照查询建立合适索引。** `storage/sqlite.py:2622–2628` 按 source_ts、id 排序；候选快照表没有对应索引。临时 schema 的 EXPLAIN 显示 `SCAN s` 和 `USE TEMP B-TREE FOR ORDER BY`。在回放副本中衡量 `source_ts DESC, id DESC` 索引收益后再迁移；不要未经测量承诺页面加速比例。
4. **把扫描与复盘的等待分清。** 保留单 Worker 与确定性候选链路，记录供应商请求、排队、数据库等待各阶段用时；确保历史补结算不占据实时扫描的锁或请求预算。当前 300 秒 watchdog 的具体阻塞调用未完成定位，不将某个历史问题直接认作本次根因。
5. **逐步减少逻辑重复。** Web 服务约 2,287 行、SQLite 模块约 2,717 行、桌面 session 约 2,707 行。优先抽取共同交易日决策与任务状态，不做整体重写。每次提取应由一个已复现问题或明确边界推动。

## 建议实施顺序

| 批次 | 目标 | 范围 | 完成证据 |
| --- | --- | --- | --- |
| 第一批 | 停机后启动可靠、历史可恢复 | R1–R4，固化 healthcheck 镜像与统一部署清单 | 同库冷启动；包含报告的已验证备份；WAL 新事务不丢；恢复业务日期正确 |
| 第二批 | 工作日安静运行、异常可处理 | R5；待结算分态及受控补偿；按工作日衡量可用性 | 周末不扫；周一正常恢复；耗尽重试可识别、可审计处理 |
| 第三批 | 复盘看得清、历史增长不拖慢 | 样本覆盖展示、索引实测、阶段耗时及小范围共享逻辑 | 复盘与日组合数量可对账；副本上的查询前后基准 |

## 验证与限制

macOS / Python 3.12.11，对当前 Web worktree 执行：

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_web_public_state.py tests/test_web_admin_cli.py tests/test_web_healthcheck.py tests/test_web_worker.py tests/test_web_services.py tests/test_candidate_outcomes.py
```

结果：130 项通过。另完成 Compose 配置解析、周末时段门、报告备份遗漏、mtime 反转恢复、WAL 隔离后果、五次重试上限及查询计划的离线探针。探针只使用临时目录、合成 SQLite 和假日历，不使用现网数据或外部行情。

现有测试通过不代表上述新发现已修好。本轮未进行登录后视觉验收、真实交易时段、完整恢复部署或 Windows 验收。原有未提交项保持原样：`deploy/docker-compose.yml`、`src/stock_watcher/server/healthcheck.py`、未跟踪的 `deploy/docker-compose.bind-db.yml`。
