# Tushare M0 脱敏报告模板

## 元数据

- 报告 schema、代码 commit、平台、Python、应用版本：
- provider profile、endpoint（仅路径）、provider/schema 版本：
- 开始/结束、是否真实交易时段：
- 凭据 fingerprint（可选，仅 SHA-256 前 8 位）、权限与到期：

## 凭据与能力

| 能力 | HTTP | 供应商 code | 权限 | 耗时 ms | source_ts | 结论 |
|---|---:|---:|---|---:|---|---|
| health | | | | | | |
| status | | | | | | |
| catalog | | | | | | |
| trade_cal（小范围） | | | | | | |
| stock_basic（小范围） | | | | | | |

只保存接口名、字段名摘要、数量、状态和时延；不保存正文、股票值或凭据。

## 静态、历史与板块

- 全 A 数量；SH/SZ/BJ 分布；重复/缺失：
- ST、退市、停牌、新股等排除字段：
- 三匿名样本日线、最近三个交易日 1 分钟、5 分钟：
- 午休断点、重复、缺分钟、OHLC、volume/amount 单位：
- 行业、概念、股票所属板块、成分和交易日历：

## 全市场实时（至少 30 分钟）

- 每轮数量、市场覆盖、缺失、重复：
- 轮次耗时 p50/p95/max；source age p50/p95/max：
- HTTP/业务错误率；429/503/5xx：
- 回滚、停滞、量额推进：
- 断线、STOPPED、恢复与三周期预热：
- 10 秒异动发现预算是否可行：

## 实时分钟、Level-2 与接口比较

- 1MIN/5MIN/当日分钟、闭合 K、延迟、批量上限、限频和 TopN：
- Level-2 实时/历史、档位、timestamp、单位、频率和授权：
- Super/Fast 同 API 成功率、p50/p95/max、字段、行数、时间戳、一致率：
- Fast 是否进入允许列表：

## 最终结论

只能选择 `PASS`、`PASS_WITH_LIMITS` 或 `FAIL`。

- 首个真实失败点：
- 候选门：OPEN / WARMING / STOPPED
- 原始响应保存：必须为 false
- 凭据保存到报告：必须为 false

