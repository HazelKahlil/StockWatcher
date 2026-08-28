# Windows packaging 入口

本目录维护 portable/Inno Setup 配置与 `0.6.0-alpha.6` 桌面稳定性候选。PR 的 Governance 必须分别在 Windows Python 3.11 与 3.12 完成离线回归、PyInstaller、Inno Setup 和制品上传，才可把本提交称为可重建候选。

Windows Python 3.11 还必须覆盖 Qt 生命周期回归：测试不得长期改写全局 `QApplication.quit`，窗口关闭与测试清理后不得留下仍被 Qt 父对象持有的生命周期对象或后台工作线程。打包时嵌入的 `SOURCE_COMMIT` 必须是 PR 的真实 head SHA，不得使用 GitHub 临时 merge ref。

CI/build 证据仍不能替代目标机验收：真实 M0、100%–175% DPI、多屏、安装/覆盖升级/卸载、通知、扫描中关闭、断网恢复和交易时段行为，必须在实际 Windows 电脑独立记录。

后续从已验证的 `main` 创建明确的 Windows 内部测试节点；平台适配继续复用 Shared Core，不复制或分叉候选算法。
