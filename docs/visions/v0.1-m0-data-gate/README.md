# v0.1-m0-data-gate：证明数据与授权可行性

> 状态：计划中
> 创建：2026-07-22 ｜ 计划 tag：`v0.1.0`

## 目标与范围

- 现场验证通达信最新正式版、Python/TdxQuant、全市场行情、三日历史、板块、紫黄线、批量性能、重连和授权。
- 交付可重复运行的 `m0_probe`、字段/版本元数据与 `M0_report.md`。
- 可建立统一 domain 对象、Provider Protocol、ReplayProvider 和最小工程/测试骨架，前提是不把未验证字段接入资金结论。
- 不在本版交付完整 UI、完整排名、iPhone、自动调参、风险提醒或稳定安装包。

## 开工门

- [ ] 创建并关联执行 issue，明确能访问目标 Windows 电脑、通达信版本和授权账号。
- [ ] 本版本在 `docs/visions/README.md` 登记为活跃并先合入 `main`。
- [ ] 读取 `docs/process/rules/data.md`、`security.md` 与 `boundaries.md`。

## 验收标准（必须可检查）

- [ ] 完成 `docs/reference/v2.0/m0_checklist.md` 全部检查并附证据。
- [ ] 至少 3 只股票每 5 秒比对界面与程序值，连续 ≥30 分钟；紫黄线在显示精度内一致率 ≥98%，或明确 FAIL/限制。
- [ ] 全市场基础快照新鲜度、全扫描、TopN 深度计算、端到端延迟有 p50/p95 和错误率证据。
- [ ] 板块、三日历史、开盘/午后/断网重连与一个完整交易时段有结果。
- [ ] 授权结论覆盖内部 2—3 人、本地展示、历史保存、公式/Level-2 使用。
- [ ] `M0_report.md` 结论只能是 PASS、PASS_WITH_LIMITS 或 FAIL；每个限制映射到降级路线与 owner。
- [ ] 自动测试验证 provider 归一化、时间戳、去重、stale/STOPPED/WARMING 和 Replay 确定性。

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-07-22 | 版本范围与开工门建立，尚未执行现场验证 | 计划中 |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| M0 可与 Replay/工程骨架并行，但不能假装资金模块已通 | 提高推进效率，同时保护真实数据口径 |
| 目标 Windows/通达信现场访问是关键依赖 | 没有现场证据不能给 PASS；阻塞时明确等待 owner，不用猜测 |
| 若通达信授权或性能不满足 | 按规格降级到 M1 only、正式供应商评估或 NO-GO，不走网页抓取旁路 |

## Session Handoff 索引

尚无。执行中若跨 session，优先更新本 README；现场长验证再登记 handoff。

## 封版记录

- 验证结果：待执行。
- 遗留问题：待执行后填写并分配 owner。
- 终态对账：关联 issue 待创建；必需子任务/stage 待登记；Issue 状态与本 README 状态一致：待核对。
