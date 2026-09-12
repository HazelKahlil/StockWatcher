# 候选认可反馈：隔离接入待验收

状态：隔离正式页视觉已通过，待 PR 审核；未授权生产迁移/上线，未合 `main`。

适配源码基准：`7e103f6249a215de8f90e74e4291380853190788`（`codex/ui-motion-performance`）。
当前功能 head：`803da8dc24c4ec51d6dbc37005222d4ae175fcc4`。

## 范围

右侧个人认可复选框、撤销、服务端持久化、用户/候选日/代码的当前标记、不可变操作上下文、幂等、并发控制和权限保护的回看查询。不改变排名、提醒、行情采集、共享公共状态，不新增交易能力，不自动调参。

未操作=未反馈，撤销≠否定。保留勾选≠对每次新快照再认可一次。

## 接入与数据

`STOCKWATCHER_CANDIDATE_APPROVALS` 默认关闭。core schema v10 不变，显式安装独立 approval extension v1。不在启动/请求中执行 DDL，不自动清理反馈。

临时库测试使用 `python -m stock_watcher.feedback.cli --db <隔离库> --backup-dir <隔离备份> --confirm-offline`。生产迁移需要备份、离线确认和 Human Owner 再授权。

## 必须核对

- [x] 已回读实际项目新增代码和六个接入点，无未审查的本地覆盖。
- [x] Python 3.12 锁定环境新增存储/API/真实 app 测试通过（含 `test_candidate_approval_app.py`，未 skip）。
- [x] Node、现有 UI 回归、Ruff、Mypy 通过。
- [x] 开启功能的正式页面 `http://127.0.0.1:18767/` 通过浏览器交互验收，而不只是默认关闭的旧 harness。
- [x] 临时库认可、重载、两账户、撤销、快照切换在隔离库验证。
- [x] 用户确认右侧「选择」开关视觉通过。
- [ ] 生产备份、扩展迁移、关闭开关/旧镜像兼容需 Human Owner 再授权。

交接环境只有 Python3.13 的模块/API隔离测试与 Chromium 组件桥接测试；它们不能替代上面的完整项目检查。详细证据和配套 Brief 在原资源包内。

接手者收尾填写：实际提交、已执行命令、失败/未测、数据来源、截图对应关系及下一步 owner。不提前改成“可以上线”。
