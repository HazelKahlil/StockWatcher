# 2026-07-30 Mac 验证记录

> 环境：macOS 本机、`feat/macos-v1-port`；所有结果仅证明 Mac 行为，不能替代 Windows
> 真实验收。Windows 继续为 `FAIL`，实时扫描轮次与真实 Top3 均为 0。

## 已通过的离线与本机验证

| 项目 | 结果 |
| --- | --- |
| 依赖与锁定 | `uv sync --all-groups`、`uv lock --check` 通过 |
| 全量测试 | 257 passed、20 skipped、2 deselected |
| 静态检查 | `uv run ruff check .` 通过；`uv run mypy src tests` 通过（93 个源文件） |
| 工作区 | `python3 scripts/validate_workspace.py` 通过（29 个必需文件） |
| 差异 | `git diff --check` 通过 |
| Replay 五状态 | 离屏生成健康主界面、三行弹窗、STOPPED、详情、历史共 5 张 PNG |
| 数据接口页 | macOS 离屏截图确认 Token 输入框完整、保存/重新检测/清除按钮可见且有明确状态文案 |
| SQLite | WAL 备份/回滚及 v0.2 SQLite 定向回归通过 |
| 单实例 | 13 项 macOS 定向测试通过，覆盖异常中断后遗留 socket 的恢复 |

上述 Replay 与界面截图为固定离线证据；不使用 Token、原始行情或 Windows 通知，不能冒充真实
市场候选。

## 已执行的真实数据复核

| 时间（Asia/Shanghai） | 路线 | 结果 | 可作出的结论 |
| --- | --- | --- | --- |
| 2026-07-30 盘后（旧根路径） | Primary 普通 Pro（`fastapic`） | `rate_limited` | Token 保留；旧根路径不启动新候选 |
| 2026-07-30 19:01 | Primary 普通 Pro（供应商文档 SDK 路径） | `POST /trade_cal` HTTP 200、8 条 | 基础连接通过；不是全市场、Top3 或交易时段证据 |
| 2026-07-30 盘后 | 原生 `realtime_quote(src="sina")` 单证券结构 | HTTP 200、1 条且含供应商时间字段 | 仅验证盘后接口结构，不是全市场、Top3 或交易时段证据 |
| 2026-07-30 15:43–15:44 | 隔离 Super 静态高级诊断 | `empty_data` | 无当日日线、无报告、无 Top3；只能标记高级诊断阻塞 |

旧根路径普通 Pro 限流与 Super 空数据均未被密集重试。供应商文档 SDK 路径只做了一次受控
轻量连接复核；资金继续为“资金未确认”，且没有被用于阻塞候选。交易时段主数据健康尚未验证，
本次没有生成新候选。

## 未通过的严格门与唯一下一步

真实交易时段的 1/100/300/800、连续全市场扫描、真实 Top3、09:45/14:45 应用内弹窗、30 天
历史、15:30 总结、Retina/多屏、睡眠/网络恢复人工会话以及 `.app` 打包均尚未通过。唯一下一步
是在下一交易日 09:25 以 Primary 文档 SDK 主路线进行一次受 1 秒共享预算控制的实时验证；
主路线通过后再继续完成其余交易时段验收，绝不以 Super、Replay 或 Mac 结果替代它们或 Windows
验收。
