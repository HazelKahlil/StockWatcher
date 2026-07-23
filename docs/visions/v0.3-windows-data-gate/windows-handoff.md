# Windows TdxQuant 一页交接

## 你只需要做三件事

1. 在 Windows 安装并登录通达信官网免费的 64 位“金融终端（量化模拟）”，保持终端运行并开启 TQ。
2. 从 StockWatcher 私有 GitHub 取得交付包并解压。不要把 Windows 密码、通达信密码、token 或远程桌面权限交给 Agent。
3. 在解压目录右键打开 PowerShell，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\stockwatcher.ps1
```

菜单依次执行“安装/更新”→“通达信预检”→“执行 M0 探针”。需要查看安全诊断界面时再选“启动应用”。

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

- 菜单“构建分发包”先生成 `dist\StockWatcher`；若机器安装了 Inno Setup，再生成安装器。
- 应用和报告位于 `%LOCALAPPDATA%\StockWatcher`，安装程序位于 `%LOCALAPPDATA%\Programs\StockWatcher`。
- 卸载不会自动删除数据库、日志和 M0 报告，避免误删证据。确认已备份后再由用户手动删除运行目录。
- 回滚时卸载当前版本并安装上一个已验证包；不得为恢复服务切换到未授权数据源。

Windows、通达信、紫黄线、性能和安装体验只有在真实机器完成上述 M0 后才算验证。
