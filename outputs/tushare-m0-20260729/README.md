# Tushare M0 — 2026-07-29

本目录只包含脱敏聚合证据；未保存 API 凭据、响应正文、股票代码、股票名称或行情值。

## 结论

`FAIL`

- Super/Fast 基础能力均为 `PASS_WITH_LIMITS`。
- Super 真实取得 5,530 条在市证券记录、交易日历、日线、申万行业、概念和成分。
- 三只匿名样本最近三个已闭合交易日的 1 分钟与 5 分钟历史均通过：
  重复时间戳、午休异常行、OHLC/负量额异常均为 0。
- 2026-07-29 13:00:02 开始的首次全市场实时尝试在第一轮即不满足门禁；
  runner 停止前共记录 5 轮，均为 `empty_data`，错误率 100%。
- 后续小范围诊断确认：单只 `rt_k` 仍为空，三只 `rt_min` 返回 HTTP 400
  业务错误。因此失败不是全市场通配符本身造成的。
- 13:13 后续诊断严格补上官方必填 `freq=1MIN`：Super 的单股/全市场 `rt_k`
  均为 HTTP 200 空数据，`rt_min` 与 `rt_min_daily` 均超时；Fast 的独立
  `rt_k` 诊断最终为业务错误。报告见 `realtime-diagnostics-20260729.json`。
- 官方文档确认 `rt_k`、`rt_min` 为独立实时权限，`rt_min_daily` 随
  `rt_min` 权限获得；普通积分、静态/历史可用不代表实时权限可用。

## 保留的首次失败

- `super-capability.json`：旧探针对纯文本 health 使用 JSON 解析，`invalid_json`。
- `fast-capability.json`：旧探针向 daily 只传 `limit`，服务端 HTTP 500。
- `super-capability-attempt2.json`：修正元数据合同后，数据端点仍使用错误的 POST
  请求形状，`trade_cal` 业务错误。
- `realtime-market-30m.json`：权威实时首次失败，不得被后续诊断覆盖或替代。

前三项推动了最小探针合同修复；第四项是当前外部数据阻塞。

## 当前放行状态

- 数据接口连接：可用（静态/历史）。
- Tushare Data Gate：`FAIL`。
- 真实候选：关闭。
- 09:45 / 14:50 / 盘中特别强异动提醒：未执行。
- Level-2、紫黄资金线：`unavailable`。

唯一下一步是由数据供应商为当前 Super 凭据开通或修复 `rt_k` 和 `rt_min`
盘中实时权限，并确认响应含可信供应商时间。权限变化后必须使用全新报告执行
30 分钟实时 M0；本目录的首次失败证据不得覆盖。
