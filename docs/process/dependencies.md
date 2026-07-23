# v0.2 依赖审计

> 最后更新：2026-07-22 ｜ 范围：`pyproject.toml` 的直接与开发依赖；精确解析版本见 `uv.lock`。

本版没有引入数据供应商、交易 SDK、网络客户端或通知 SDK。所有依赖都在 Mac 的 Mock/Replay 基础中使用；不改变交易账户、凭证或数据授权边界。

| 依赖 | 类型与用途 | 许可证 | 安全影响 |
| --- | --- | --- | --- |
| `pydantic` | 运行时；版本化配置与输入校验 | MIT | 仅本地解析；不得把配置中的秘密写入日志、数据库或 Git。 |
| `PySide6` | 运行时；Mac Mock/Replay 主窗口、详情、历史只读视图和低打扰弹窗 | LGPL-3.0/GPL-2.0-or-later | 仅本地桌面 UI；不连接交易账户、供应商或外部通知；版本锁定在 `uv.lock`。发布时需随包提供对应许可证与 LGPL 履约材料。 |
| `PyYAML` | 运行时；读取受控 YAML 配置 | MIT | YAML 只使用 `safe_load`；禁止加载不可信对象。 |
| `hatchling` | 构建后端；生成 Python 包 | MIT | 仅构建时使用；锁文件固定解析。 |
| `pytest` | 开发；确定性回归测试 | MIT | 仅开发期；测试不得使用真实用户、行情或凭证。 |
| `ruff` | 开发；静态检查与 import 排序 | MIT | 仅开发期；不访问供应商或账户。 |
| `mypy` | 开发；严格类型检查 | MIT | 仅开发期；降低接口契约回归风险。 |
| `types-PyYAML` | 开发；PyYAML 类型桩 | Apache-2.0 | 仅开发期；无运行时数据访问。 |

复现与检查命令：

```bash
uv sync --all-groups --frozen
uv lock --check
```

升级任一依赖时，必须更新本表、保留 `uv.lock`、审查许可证和安全影响，并重跑回放、测试、Ruff 与 Mypy。
