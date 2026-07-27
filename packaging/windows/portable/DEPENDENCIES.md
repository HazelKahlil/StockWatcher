# 便携版运行依赖与安全影响

| 组件 | 来源与要求 | 随包携带 | 许可证 | 安全影响 |
| --- | --- | --- | --- | --- |
| Python 3.12 / Pythonw | 目标机现有、python.org 官方签名的 64 位安装 | 否 | PSF-2.0 | 运行包内应用；不改 PATH，不安装包，不联网。 |
| PySide6 6.11.1 | 目标机按 `uv.lock` 预置 | 否 | LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only（Qt for Python 声明） | 显示真实 StockWatcher UI；主入口只读检查 import。内部使用仍须遵守对应许可证。 |
| Pydantic 2.13.4 | 目标机按 `uv.lock` 预置 | 否 | MIT | 本地配置和模型校验；主入口不写秘密。 |
| PyYAML 6.0.3 | 目标机按 `uv.lock` 预置 | 否 | MIT | 只允许项目安全 YAML 路径；不得加载不可信对象。 |
| tzdata 2026.3 | 目标机按 `uv.lock` 预置 | 否 | Apache-2.0 | 提供 Windows 时区数据，不访问网络。 |
| `tqcenter` Python 模块 | 随获授权的官方 TdxQuant 环境预置；版本与目标终端匹配 | 否 | 以官方安装包和授权条款为准 | 原生 Preflight 的固定 `python_client` 检查；本项目不下载、重打包或替代该模块。 |
| Windows Script Host | Windows 系统组件 | 否 | Microsoft Windows | 仅隐藏窗口启动验签 Pythonw。 |
| Windows PowerShell 5.1 | Windows 系统组件 | 否 | Microsoft Windows | 仅做 Authenticode 发布者校验；不使用 ExecutionPolicy Bypass。 |
| StockWatcher 应用、UI 与原生 Preflight | 本 ZIP 中的 `app/src/stock_watcher` | 是 | 项目内部代码 | 只读访问官方回环 `127.0.0.1:17709`；报告固定脱敏，不记录账号、凭证、HTTP body 或行情。 |

本包是内部单机便携候选：携带产品源码和 `app/uv.lock`，不携带解释器和第三方 wheel。管理员须在交付前按该冻结 lock 离线准备上述直接依赖及其传递依赖，并从获授权的官方环境提供 `tqcenter`；主入口只做只读模块/版本检查，缺项即中文 fail-closed，不调用 pip。许可证和完整解析版本以 `app/uv.lock` 及官方安装条款为准。
