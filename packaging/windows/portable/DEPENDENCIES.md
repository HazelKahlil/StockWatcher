# 便携版运行依赖与安全影响

| 组件 | 来源与要求 | 随包携带 | 许可证 | 安全影响 |
| --- | --- | --- | --- | --- |
| Python 3.12 / Pythonw | 目标机现有、python.org 官方签名的 64 位安装 | 否 | PSF-2.0 | 仅运行本地标准库程序；不改 PATH，不安装包，不联网。 |
| Tcl/Tk | python.org 官方 Python 3.12 的桌面组件 | 否 | Tcl/Tk License | 仅显示本地中文状态窗口。 |
| Windows Script Host | Windows 系统组件 | 否 | Microsoft Windows | 仅用隐藏窗口启动 Pythonw 和创建可选快捷方式。 |
| Windows PowerShell 5.1 | Windows 系统组件 | 否 | Microsoft Windows | 仅做 Authenticode 发布者校验和创建可选快捷方式；不使用 ExecutionPolicy Bypass。 |
| StockWatcher 便携代码 | 本 ZIP 中的 `portable/stockwatcher_portable.py` | 是 | 项目内部代码 | 只访问官方回环 `127.0.0.1:17709`，不接收或记录账号、凭证、行情正文。 |

便携路径不导入 PySide6、Pydantic、PyYAML、tzdata 或其他第三方 Python 包，因此首次双击不执行依赖安装，也不需要互联网。项目完整开发/打包依赖仍见仓库 `docs/process/dependencies.md`，但不是本便携入口的运行依赖。
