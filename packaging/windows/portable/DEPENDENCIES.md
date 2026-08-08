# 便携版运行依赖与安全影响

| 组件 | 来源与要求 | 随包携带 | 许可证 | 安全影响 |
| --- | --- | --- | --- | --- |
| Python 3.12 / Pythonw | 目标机现有、python.org 官方签名的 64 位安装 | 否 | PSF-2.0 | 运行包内应用；不改 PATH，不安装包，不联网。 |
| PySide6 6.11.1 | 目标机按 `uv.lock` 预置 | 否 | LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only（Qt for Python 声明） | 显示真实 StockWatcher UI；主入口只读检查 import。内部使用仍须遵守对应许可证。 |
| Pydantic 2.13.4 | 目标机按 `uv.lock` 预置 | 否 | MIT | 本地配置和模型校验；主入口不写秘密。 |
| PyYAML 6.0.3 | 目标机按 `uv.lock` 预置 | 否 | MIT | 只允许项目安全 YAML 路径；不得加载不可信对象。 |
| tzdata 2026.3 | 目标机按 `uv.lock` 预置 | 否 | Apache-2.0 | 提供 Windows 时区数据，不访问网络。 |
| requests 2.34.2 | 目标机按 `uv.lock` 预置 | 否 | Apache-2.0 | Tushare HTTPS 传输；TLS 验证开启，代理行为配置化。 |
| keyring 25.7.0 | 目标机按 `uv.lock` 预置 | 否 | MIT | 正式凭据写入 Windows Credential Manager，不写配置、SQLite 或日志。 |
| Tushare 1.4.29 | 目标机按 `uv.lock` 预置 | 否 | BSD | Human Owner 明确授权的原生实时 SDK 路线；只读新浪快照，不连接交易账户。 |
| pandas 3.0.5 / NumPy 2.4.6 / lxml 6.1.1 | Tushare 锁定运行依赖 | 否 | BSD-3-Clause / BSD-3-Clause / BSD-3-Clause | 仅在内存解析和归一化实时快照；原始行情不落盘，NumPy 约束 `<2.5` 以保持 Python 3.11 Mypy 门。 |
| `tqcenter` Python 模块 | 仅可选 TdxQuant 诊断；版本与目标终端匹配 | 否 | 以官方安装包和授权条款为准 | 不再是正常启动依赖；本项目不下载、重打包或替代该模块。 |
| Windows Script Host | Windows 系统组件 | 否 | Microsoft Windows | 仅隐藏窗口启动验签 Pythonw；不请求提权。 |
| Windows PowerShell 5.1 | Windows 系统组件 | 否 | Microsoft Windows | 仅做 Authenticode 发布者校验；不使用 ExecutionPolicy Bypass，不使用 elevation verb。 |
| StockWatcher 应用、UI 与诊断模块 | 本 ZIP 中的 `app/src/stock_watcher` | 是 | 项目内部代码 | 默认全市场真实 Top3；TdxQuant 只读诊断可选；不记录账号、凭证或 HTTP body。 |

本包是内部单机便携候选：携带产品源码和 `app/uv.lock`，不携带解释器和第三方 wheel。
管理员须在交付前按冻结 lock 离线准备直接和传递依赖。正常启动不需要通达信或 `tqcenter`；
可选 TdxQuant 诊断才需要官方环境。主入口不调用 pip、不自动启动 `TdxW.exe`，也不请求管理员权限。
许可证和完整解析版本以 `app/uv.lock` 及官方安装条款为准。
