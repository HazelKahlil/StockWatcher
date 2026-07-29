# StockWatcher TdxQuant M0 报告模板

## 结论

- Verdict：`PASS | PASS_WITH_LIMITS | FAIL`
- Windows 版本：
- 通达信终端/TdxQuant 版本：
- Python/StockWatcher 版本：
- 账号与内部使用授权范围（不写账号名或凭证）：
- 交易时段与持续时间：

## 能力与性能

| 能力 | 状态 | 样本/行数 | p50 | p95 | 错误率 | 限制 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 全 A 股列表 | | | | | | |
| 批量价量 | | | | | | |
| 单股快照 | | | | | | |
| 三日分钟历史 | | | | | | |
| 板块与成分 | | | | | | |
| 交易日历 | | | | | | |
| 断线/恢复/补数 | | | | | | |

## 时间与质量

- `source_ts` 是否由供应商明确提供：
- `received_ts` 与端到端延迟：
- 重复 `code + source_ts` 处理：
- 旧数据、停牌、非交易时段与数据中断行为：
- `STOPPED → WARMING → HEALTHY` 预热证据：

## 紫黄线与资金

- 紫/黄的准确字段、颜色、公式、单位、累计与刷新：
- 历史与批量能力：
- 界面/程序比对一致率：
- 账号/版本/Level-2 权限：
- 未通过时：`fund_module = unavailable`，不得填替代字段。

## 安装与回滚

- PyInstaller 构建：
- Inno Setup 安装：
- 启动、目录、日志与数据库：
- 卸载保留与手动清理：
- 上一已验证包回滚：

## 未验证与下一步

每项限制必须写 owner 和下一触发点。Mac/CI/fixture 证据单列，不能写进 Windows 实测结果。
