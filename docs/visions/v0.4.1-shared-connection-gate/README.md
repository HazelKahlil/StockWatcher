# v0.4.1-shared-connection-gate：跨平台 Tushare 连接门返修

> 状态：本地完成，待 Mac 分支消费
>
> 创建：2026-07-30
>
> 基线：`5b20b707e83baa16b1486894f8e53f343830d67c`
> 工作分支：`fix/shared-data-connection-gate`

## 决策与边界

Human Owner 已决定先在 Mac 验证同一套 Tushare 主路线，再将本版本的共享返修同步回
Windows。此版本只包含跨平台 Provider、Token 连接门、能力状态、限流、UI 设置与相应
测试；不包含 macOS 路径、Keychain 文案、生命周期、通知或 `.app` 打包。

Windows 真实验收继续为 `FAIL`：冻结基线在统一 Token 连接校验阶段遇到 `rate_limited`，
完整实时扫描轮次和真实 Top3 都是 0。任何后续 Mac 证据不得改写该结论。

## 目标

- “测试并保存”只执行一个轻量 `trade_cal` 调用；成功后原子保存 Token。
- 股票列表、交易日历、板块、历史分钟与实时 1/100/300/800 批次在后台串行分项检测。
- 429 保留 Token，优先遵守 `Retry-After`，无值时对全应用请求启动实施 60 秒冷却。
- 普通 Pro、原生实时、能力检测、预热和扫描使用一个默认 1 秒、下限 0.6 秒的应用级预算。
- 主实时只走 `realtime_quote(src="sina")`；`rt_k` 不进入 15000 积分主路线。
- 资金未确认继续允许业务层按既有锁定规则形成候选，不把日级 moneyflow 冒充盘中资金。

## 验收

- [x] 轻量验证失败时旧 Token 保持可用；成功后才替换。
- [x] 一次只允许一个 Token 测试和一个后台能力检查流程。
- [x] 各能力独立显示；历史或板块失败不删除 Token，实时仍独立检测。
- [x] 429 不作短间隔重试，并在冷却结束后从失败项续检。
- [x] Pro 与原生实时共享同一请求启动预算。
- [x] 数据接口页的 Token 输入框、保存提示和操作按钮在 macOS/Windows 使用可伸展表单列；
  “测试并保存”为清晰主操作，未保存前的“重新检测/清除”保留可见但明确置灰。
- [x] 默认离线 pytest、Ruff、Mypy、workspace、lock 和 diff 检查通过（235 passed，20 skipped，2 deselected）。

## 后续

共享返修以独立本地 commit 提交后，从其 HEAD 创建 `feat/macos-v1-port`。Mac 专属改动
不得回填本分支；Windows 后续只取此处的共享 commit。
