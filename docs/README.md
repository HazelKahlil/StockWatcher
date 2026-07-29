# StockWatcher 文档地图

> 新 session 从这里开始。事实以 workspace 为准，不以聊天记忆为准。
> 读取顺序：`AGENTS.md` → 本文件 → `project/index.md` → `process/index.md` → `visions/README.md` → 活跃或目标版本 README。

## 三桶结构

| 目录 | 放什么 | 什么时候更新 |
| --- | --- | --- |
| `project/` | 长期事实：定位、技术栈、模块关系、运行与部署事实 | 长期事实变化时 |
| `process/` | 过程规则：硬边界、领域规则、决策、踩坑、发布流程 | 方法、风险、约束或流程变化时 |
| `visions/` | 版本：范围、验收、进度、风险、封版状态 | 需求、范围、验证和封版变化时 |

## 原始基线

| 目录 | 定位 |
| --- | --- |
| `reference/v2.0/` | 2026-07-22 交接包中选定的机器可读需求基线与配套材料；不是 session 日志，也不能被实现静默改写 |

交接基线的导入取舍、源哈希、阅读顺序和冲突优先级见 [reference/v2.0/README.md](reference/v2.0/README.md)。长期事实和版本 README 应引用原始基线，不复制整段需求。

## 当前导航

- 长期事实：[project/index.md](project/index.md)
- 规则入口：[process/index.md](process/index.md)
- 高风险区：[process/boundaries.md](process/boundaries.md)
- 发布流程：[process/release.md](process/release.md)
- 版本索引：[visions/README.md](visions/README.md)
- 已完成（本地待同步）：[visions/v0.1-mac-replay-foundation/README.md](visions/v0.1-mac-replay-foundation/README.md)
- 已完成（本地待同步）：[visions/v0.2-mac-local-alpha/README.md](visions/v0.2-mac-local-alpha/README.md)
- 活跃版本（Windows TdxQuant 数据闸门）：[visions/v0.3-windows-data-gate/README.md](visions/v0.3-windows-data-gate/README.md)
- Windows 一页交接：[visions/v0.3-windows-data-gate/windows-handoff.md](visions/v0.3-windows-data-gate/windows-handoff.md)
- Windows Codex 直接交接：[visions/v0.3-windows-data-gate/session-handoff-windows-codex.md](visions/v0.3-windows-data-gate/session-handoff-windows-codex.md)
