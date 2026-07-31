# 2026-07-31 Mac 实时路线验证记录

> 环境：macOS 本机、`feat/macos-v1-port`。凭据仅从实际 macOS Keychain 读取；未写入
> 命令参数、源码、配置、日志、报告、截图或 Git。未访问 GitHub，未使用 Super、`rt_k`、
> Replay 或大模型生成候选。以下结果只能证明 Mac，不代表 Windows 通过。

## 本轮固定路线

实时只允许：

```python
from tushare.stock import cons as ct

ct.verify_token_url = "https://realtime.stockai888.top"
df = ts.realtime_quote(ts_code="多只股票代码", src="sina")
```

普通 Pro 仅用于交易窗口前准备股票名单、板块和已完成交易日趋势缓存。实时窗口读取原子缓存，
按1只、100只、300只、800只递进后，再以每批最多800只、批次请求起点至少间隔1秒完成全市场
七批。`rt_k`和Super不进入候选链。

## 离线工程门

| 项目 | 结果 |
| --- | --- |
| 全量 pytest | 284 passed、20 skipped、2 deselected |
| Ruff | PASS |
| Mypy | PASS，98个源文件 |
| workspace | PASS，29个必需文件 |
| lock / diff | `uv lock --check`、`git diff --check` PASS |
| Replay | 五状态PNG 5/5生成 |
| SQLite | WAL、备份、回滚、迁移定向5/5 |

新增回归覆盖：

- 5500只严格拆为`800×6 + 700`；
- SDK真实形态HTTP 429保留为`rate_limited`，不被装饰器改写；
- Pro与实时共享请求起点间隔，但Pro 429不冻结实时路线；
- 交易窗口只后台执行1/100/300/800，不调用普通Pro能力检查；
- 原子缓存SHA-256、完整性、时效、失败替换保留旧版本；
- 缓存缺失、损坏或过期时停止新候选。

## 真实接口结果

| 时间（CST） | 操作 | 结果 | 严格结论 |
| --- | --- | --- | --- |
| 10:37 | 单进程生成原子静态缓存 | `stock_basic`为`rate_limited`，2.219秒停止 | 缓存未生成；Token保留 |
| 10:42:12 | 单只原生实时 | 1条；价格11.44；量116,511,112股；额1,328,117,351.52元；source age 2.807秒；`HEALTHY` | 实时路线真实可用，且未被Pro 429阻塞 |
| 10:48 | 再次单进程生成原子静态缓存 | `stock_basic`为`rate_limited`，2.102秒停止 | 缓存仍未生成；停止重试 |

## 当前验收结论

- 原生实时单只：PASS。
- Pro 429与实时路线隔离：PASS。
- 完整原子静态缓存：BLOCKED_RATE_LIMIT。
- 1/100/300/800真实递进：未开始，原因是没有经过普通Pro验证的真实股票名单缓存。
- 全市场七批、连续三轮、真实Top3、板块门、同板块最多2只、补位和稳定替换：未开始。
- 09:45/14:45固定弹窗：本轮未取得真实候选证据。
- Windows：仍为FAIL，完整扫描0轮、真实Top3为0。

## 唯一下一步

等待普通Pro限流恢复后，只启动一个缓存准备进程，完成股票名单、板块和已完成交易日趋势的
原子缓存。缓存通过后，在交易窗口运行原生实时1/100/300/800与全市场七批；不得用本次单只
成功、Super、Replay或旧盘后报告替代真实Top3。
