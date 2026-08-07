# Windows packaging 入口

本目录保留既有 portable/Inno Setup 配置，不在本轮新增重复实现。Windows 尚未进入活跃开发；真实 M0、安装、通知、恢复和交易时段验收必须在目标 Windows 独立完成。

未来从已验证 main 创建 `windows/internal-test-v1`，先复用 Shared Core，再只增加 Windows 平台适配。
