# HAZ-406：Mac 可移植性预检

> 环境：macOS（仅 Mock/Replay/Synthetic）｜结论：`PASS_WITH_LIMITS`

## 本次可复用边界

| 范围 | 结论 | 说明 |
| --- | --- | --- |
| `domain/` | 可直接复用 | 统一对象、Asia/Shanghai 时间戳和健康状态不依赖供应商或操作系统。 |
| `engine/` | 可直接复用 | 候选排名、提醒防抖/去重和固定时点是纯确定性计算。 |
| `storage/` | 可直接复用 | SQLite WAL、历史和健康记录不依赖 Windows 或供应商 SDK。 |
| `ui/presenter.py` | 可直接复用 | 只消费归一化候选批次；Mac PySide6 界面本身不能作为 Windows 行为证据。 |
| `providers/` | 边界已预留 | `ProviderDescriptor` 按显式 readiness/capability 选择；未通过 M0 的 `tdxquant` 只能报 unavailable，绝不猜字段或静默降级为真实行情。 |

## 只能在 Windows 现场实现或验证

- 官方 TdxQuant SDK 的安装、Python/SDK 兼容性、许可和账号授权范围。
- 行情、三日历史、板块、紫黄线/Level-2 字段的名称、单位、累计逻辑、刷新和一致性。
- 批量吞吐、全时段延迟、断网重连和资源占用；包括 `STOPPED → WARMING → HEALTHY` 的真实事件映射。
- Windows 右下角、多屏/DPI、通知、托盘、声音、凭证安全存储、打包与安装/卸载。

## Windows 首次现场实施清单

1. 由 Human Owner 提供 Windows 环境、官方 TdxQuant、最小权限账号及书面授权；未齐备则 Provider 保持 unavailable。
2. 在独立分支实现 SDK adapter：仅把已验证字段映射为 domain 对象，保留 source/received 时间戳、provider/config 版本与质量状态；资金字段未通过 M0 时不进入 engine。
3. 运行 `uv sync --all-groups --frozen`、`uv lock --check`、`uv run pytest`、`uv run ruff check .`、`uv run mypy src tests`、`python scripts/validate_workspace.py` 和 `git diff --check`。
4. 执行 `docs/reference/v2.0/m0_checklist.md`：至少 3 只股票每 5 秒人工比对 ≥30 分钟，记录字段映射、p50/p95、错误率、重连与完整交易时段证据。
5. 在目标机重跑 UI/通知/打包 smoke；将结果标为 Windows 证据，不能引用本 Mac 回放结果替代。

## 本次不能证明的事项

本预检没有 Windows、TdxQuant、真实行情、紫黄线、Level-2、授权凭证、Windows 通知或安装包。因此它只证明共享核心可在缺少 Windows Provider 时安全运行，不能证明任何真实数据或 Windows 功能。
