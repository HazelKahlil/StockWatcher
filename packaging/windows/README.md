# Windows packaging 入口

本目录维护 portable/Inno Setup 配置与 0.6.0-alpha.5 桌面稳定性候选。Windows CI 负责离线回归和构建；真实 M0、DPI、多屏、安装、通知、关闭、恢复和交易时段验收仍必须在目标 Windows 独立完成。

未来从已验证 main 创建 `windows/internal-test-v1`，先复用 Shared Core，再只增加 Windows 平台适配。
