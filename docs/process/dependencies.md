# v0.3.1 依赖审计

> 最后更新：2026-07-24 ｜ 范围：`pyproject.toml` 的直接与开发依赖；精确解析版本见 `uv.lock`。

Tushare 兼容 HTTP 使用 `requests`；正式凭据使用跨平台 `keyring` 接入 Windows Credential
Manager 和未来 macOS Keychain。可选 `tqcenter` 继续由官方 Windows 终端环境提供并延迟
加载，不写入跨平台锁文件。本版不引入交易 SDK、交易账户或通知 SDK。

| 依赖 | 类型与用途 | 许可证 | 安全影响 |
| --- | --- | --- | --- |
| `pydantic` | 运行时；版本化配置与输入校验 | MIT | 仅本地解析；不得把配置中的秘密写入日志、数据库或 Git。 |
| `PySide6` | 运行时；Mac Mock/Replay 主窗口、详情、历史只读视图和低打扰弹窗 | LGPL-3.0/GPL-2.0-or-later | 仅本地桌面 UI；不连接交易账户、供应商或外部通知；版本锁定在 `uv.lock`。发布时需随包提供对应许可证与 LGPL 履约材料。 |
| `PyYAML` | 运行时；读取受控 YAML 配置 | MIT | YAML 只使用 `safe_load`；禁止加载不可信对象。 |
| `tzdata` | 运行时；为 Windows 等缺少系统 IANA 时区数据库的冻结环境提供 `Asia/Shanghai` 数据 | Apache-2.0 | 仅提供锁定的时区数据；不增加供应商、账户、交易或网络能力。 |
| `requests` | 运行时；Super/Fast HTTPS 传输、timeout、TLS 与状态码处理 | Apache-2.0 | TLS 验证保持开启；代理行为配置化；响应正文不进入日志、报告或业务层。 |
| `keyring` | 运行时；Windows Credential Manager 与未来 macOS Keychain 统一接口 | MIT | secret 只经进程内存传给请求层；不写配置、SQLite、日志、Git 或包。冻结构建显式收集平台 backend。 |
| `hatchling` | 构建后端；生成 Python 包 | MIT | 仅构建时使用；锁文件固定解析。 |
| `pytest` | 开发；确定性回归测试 | MIT | 仅开发期；测试不得使用真实用户、行情或凭证。 |
| `ruff` | 开发；静态检查与 import 排序 | MIT | 仅开发期；不访问供应商或账户。 |
| `mypy` | 开发；严格类型检查 | MIT | 仅开发期；降低接口契约回归风险。 |
| `types-PyYAML` | 开发；PyYAML 类型桩 | Apache-2.0 | 仅开发期；无运行时数据访问。 |
| `types-requests` | 开发；requests 类型桩 | Apache-2.0 | 仅静态检查；不访问网络。 |
| `PyInstaller` | 开发/打包；生成 Windows 可分发目录 | GPL-2.0-or-later with bootloader exception | 只在构建阶段运行；Mac 构建不能证明 Windows 产物。Inno Setup 仍需在 Windows 独立安装。 |

复现与检查命令：

```bash
uv sync --all-groups --frozen
uv lock --check
```

升级任一依赖时，必须更新本表、保留 `uv.lock`、审查许可证和安全影响，并重跑回放、测试、Ruff 与 Mypy。
