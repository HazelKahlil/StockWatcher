# Windows TdxQuant 一页交接

## 当前内部便携版：你只需要做三件事

1. 在 Windows 安装并登录通达信官网免费的 64 位“金融终端（量化模拟）”，保持终端运行并开启 TQ。
2. 取得冻结的 `StockWatcher-Internal-Portable.zip`，复核交付记录中的 SHA-256 后完整解压。不要把 Windows 密码、通达信密码、token 或远程桌面权限交给 Agent。
3. 先确认目标机已经按冻结 `uv.lock` 预置 Python 3.12 x64、PySide6、Pydantic、PyYAML、tzdata 及锁定传递依赖，并可导入官方 TdxQuant `tqcenter` 模块，再双击根目录唯一主入口 **启动 StockWatcher.vbs**。若提示原生预检失败，请由本人在官方终端完成登录并开启 TQ 后重新双击；不要修改安全策略。

ZIP 携带完整 StockWatcher 应用、PySide6 UI 与原生 Preflight，但不携带解释器和第三方 wheel。入口仅复用目标机已经允许执行的 python.org 官方签名 Python 3.12/Pythonw，启动前只读检查预置依赖，不首次联网安装。只有固定原生报告整体 `PASS`、恰好一个 `api_session=PASS`、`windows_live_verified=true` 时才启动真实 TdxQuant 诊断 UI；候选与资金模块在 M0 前继续关闭。

## 开发与完整 M0 工程入口

需要执行完整 Preflight、M0 探针或构建时，才在源码工程目录运行：

```powershell
powershell -NoProfile -File .\scripts\windows\stockwatcher.ps1
```

该入口面向开发/验证，不是普通用户日常启动路径。若组织执行策略阻止脚本，应停止并由机器管理员按现有政策处理，不得使用 `Bypass`。菜单依次执行“安装/更新”→“通达信预检”→“执行 M0 探针”。

## 到现场前已经验证

- 冻结代码候选 `7d5c8b07dd714d4f209528d23074692e8644103c` 已由独立真实 Windows 只读任务完成无终端工程/打包验收。
- Python 3.11/3.12 各完成冻结安装、30 项锁版本、74 项 pytest、Ruff、Mypy、PyInstaller 与 Inno Setup。
- PowerShell Setup 已通过；不可用 loopback endpoint 的 Preflight 会按预期非零失败，不会假报成功。
- 这些证据只减少现场工程风险，不证明真实 TdxQuant、行情、紫黄线、交易时段、通知、多屏或安装卸载体验。

## 不需要什么

- 不需要购买 Tushare 或 iFinD。
- 不需要券商交易账户、交易密码、持仓、订单或下单权限。
- 不需要把通达信账号密码写进 StockWatcher。
- 不需要安装 pytdx、非官方行情服务器、OCR 或鼠标脚本。

## 报告在哪里

预检和 M0 报告写入 `%LOCALAPPDATA%\StockWatcher\reports`：

- `tdxquant-preflight.json`
- `tdxquant-m0-report.json`
- `tdxquant-m0-report.md`

报告只保留环境、能力、耗时、字段名、行数、限制和结论，不导出账号、凭证或原始行情明细。

## 常见失败

| 提示 | 处理 |
| --- | --- |
| 未找到官方终端 | 安装免费 64 位“金融终端（量化模拟）”，预检时传入安装目录 |
| 终端未启动或 TQ 服务不可达 | 启动并登录终端，确认终端支持 TQ，再检查本机 `127.0.0.1:17709` |
| 未登录/行情权限未就绪 | 回到官方终端完成登录，确认行情页能正常刷新 |
| 接口/字段不可用 | 记录终端版本和报告；不要换字段冒充，资金模块保持未就绪 |
| 非交易时段 | 预检可继续；实时值、性能和重连改在交易时段验证 |
| 数据中断/过期 | StockWatcher 会停止新候选；恢复后先预热新鲜样本 |
| 用户已暂停 | 在应用中选择重新预检，或重新运行入口 |

## 构建、卸载与回滚

- 菜单“构建分发包”要求已安装 Inno Setup。入口通过本任务独占的短路径 staging 生成 PyInstaller bundle 和安装器，临时、备份、替换与最终发布路径均保持在短盘符映射内；`dist\StockWatcher-0.3.0-alpha-portable.zip` 与 `dist\installer\StockWatcher-0.3.0-alpha-setup.exe` 作为一个事务成组发布，任一步失败都会回滚两份既有产物、清理当次 staging 并返回非零。
- 应用和报告位于 `%LOCALAPPDATA%\StockWatcher`，安装程序位于 `%LOCALAPPDATA%\Programs\StockWatcher`。
- 卸载不会自动删除数据库、日志和 M0 报告，避免误删证据。确认已备份后再由用户手动删除运行目录。
- 回滚时卸载当前版本并安装上一个已验证包；不得为恢复服务切换到未授权数据源。

因此 Human Owner 到 Windows 后先对冻结便携 ZIP 完成依赖只读检查、原生 Preflight 报告和真实 UI 启动复验，再由独立验证任务执行 M0。通达信行情、紫黄线、性能和安装体验只有现场 M0 后才算验证。
