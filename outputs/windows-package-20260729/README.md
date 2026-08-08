# Windows package evidence — 2026-07-29

本目录只记录脱敏构建与界面 smoke 结论；没有保存凭据、HTTP body、行情、代码、
名称、账号、用户名或主机名。安装器与 portable ZIP 属于本机未跟踪构建产物，
不提交 Git。

## Build

- Python 3.12.10；PyInstaller 6.21.0；Inno Setup 6.7.3。
- 第一次构建失败点：`uv` 环境没有 `pip`，旧脚本尝试临时安装 PyInstaller。
- 修复后构建只验证锁定环境；缺失时执行 `uv sync --all-groups`，不临时改依赖。
- 第二次构建 exit 0；PyInstaller、Inno Setup、双产物事务发布均成功。
- installer：41,947,187 bytes，
  SHA-256 `7a0548e21c91e82d7b9b515580b93b43f54f273dfff2b49766f67e2aac1edc31`。
- portable ZIP：58,991,655 bytes，
  SHA-256 `e29f56235484a275572c39fb207c54733137c06325d367581e99f0c075582745`。
- ZIP 共 933 个成员；`StockWatcher.exe` 与运行时 PNG 图标均存在。
- 安装器 Authenticode：`NotSigned`。Human Owner 已明确内部自用不要求商业签名。

## Icon

- PNG：1,152,979 bytes，
  SHA-256 `fe021dcb1d8ffb154f569bb7649d646d3f6f2b5f5f5aac8830cbb4d49445e61e`。
- 多尺寸 ICO：122,937 bytes，
  SHA-256 `8f0b708fceb19a21db9fc898af68525058c5fa8a6d549354c038ad34df191a4a`。
- PyInstaller EXE、Inno Setup 安装器、Qt 窗口和任务栏均引用同一品牌图标。

## Windows smoke

- portable 解压到全新目录；解压后的 EXE 首次进程启动被本机
  Application Control 阻止。
- 未关闭、修改或绕过 Application Control；未尝试用重复启动刷成功。
- 使用本机已允许的开发 Python 仅执行源码界面工程 smoke：
  窗口、标题栏图标、启动后自动检测、基础接口已连接、实时空数据原因、
  候选关闭、人工“立即检测实时数据”按钮、检测中状态、检测完成状态和正常退出
  均通过。
- 人工按钮 readback：基础连接 HTTP 200；实时日线空数据；候选继续关闭；
  没有保存响应正文。
- smoke 结束后 StockWatcher/Pythonw 应用进程数为 0。

## Verdict

安装器和 portable ZIP 的构建为 `PASS`；当前机器上的冻结 EXE 启动为 `FAIL`
（Application Control）。Tushare 实时门仍为 `FAIL`，因此本包是可安装构建候选，
不是已经能自动产生真实异动提醒的正式版。
