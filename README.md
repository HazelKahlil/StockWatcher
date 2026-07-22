# StockWatcher

StockWatcher 是供 2—3 名内部用户使用的 A 股候选观察与异动提醒工具。它从全市场行情中筛出三只值得进一步看盘的候选，并提供固定时点和盘中特别强异动提醒。

> 本项目只提供候选观察和异动提醒，不构成投资建议；不得读取交易密码、连接交易账户或自动下单。

## 当前状态

- 项目治理与 V2.0 交接基线已建立。
- 产品代码尚未开始；下一目标是 `v0.1-m0-data-gate`。
- M0 必须先验证通达信数据、紫色超大单/黄色大单、板块、历史、性能与授权。M0 未通过时，不得声称资金模块可用。
- 仓库为私有仓库，符合交接材料中的“团队内部使用”边界。

## 从这里开始

1. 阅读 [AGENTS.md](AGENTS.md)。
2. 按 [docs/README.md](docs/README.md) 的文档地图恢复项目状态。
3. 阅读 [锁定业务项](docs/reference/v2.0/requirements.lock.json) 和 [V2.0 规格](docs/reference/v2.0/SPEC_V2.0_AGENT.md)。
4. 开始任何实现前，把 [v0.1 M0 数据闸门](docs/visions/v0.1-m0-data-gate/README.md) 从“计划中”更新为“进行中”，并关联执行 issue。

## 项目基线

| 项 | 当前约定 |
| --- | --- |
| 主运行环境 | Windows 桌面端，Asia/Shanghai |
| 计划技术栈 | Python 3.11/3.12、PySide6、SQLite WAL、YAML + Pydantic、pytest |
| 主数据口径 | 通达信最新正式版；准确字段与授权以 M0 现场验证为准 |
| 默认提醒 | 09:45、14:50；盘中特别强异动最多 3 批/日 |
| 输出 | 数据健康时每批固定三只，标记“强 / 中 / 近” |
| 安全边界 | 不读取账户，不自动交易，不用旧数据伪装正常结果 |

## Bootstrap 验证

```bash
python3 scripts/validate_workspace.py
git diff --check
```

产品实现开始后，测试、lint 和打包命令必须在 `AGENTS.md` 与目标版本 README 中补齐，未配置的命令不得被宣称为已验证。

## 版本路线

项目先完成数据可行性，再做 Alpha 核心流程，随后接入完整 V1 能力并稳定化。详见 [版本索引](docs/visions/README.md)；版本与发布规则见 [release.md](docs/process/release.md)。
