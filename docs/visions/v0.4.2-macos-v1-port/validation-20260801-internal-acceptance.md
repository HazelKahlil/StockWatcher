# 2026-08-01 Mac V1 内部验收复核

> 环境：macOS arm64、Asia/Shanghai。本记录只证明当前 Mac 恢复树和本机 App 行为；不把
> Mac 结果写成 Windows 通过，也没有读取、打印或写入真实 Token。

## 基线与分支

- 恢复目录：`/Users/kahlilhazel/Documents/700-AI-Workspace/90-Archive/StockWatcher-Mac-V1-Internal-Trial-20260801.xCnN4s/StockWatcher`
- 当前分支：`fix/macos-v1-internal-acceptance`
- HEAD：`8f8d574dee0a1b0f1393640cac832bf289e37dc9`
- HEAD Parent：`ba2408999c47c237a5fa4abbde32cc14d2f67a62`
- 工作树：clean
- 共享返修 commit：`8f8d574`（概念板块、候选池强异动、强/中/近、补位、稳定席位、实时候选池和历史清理）
- Mac 专属交付历史：`ba240899` 及其 Mac 端口提交；本分支本轮只增加验收记录，没有把
  Windows/TdxQuant 代码加入 Mac 包。

## 本轮离线验证

| 命令 | 结果 |
| --- | --- |
| `uv sync --all-groups --frozen` | PASS，54 packages audited |
| `uv lock --check` | PASS，65 packages resolved |
| `uv run pytest` | PASS：310 passed、20 skipped、2 deselected |
| `uv run ruff check .` | PASS |
| `uv run mypy src tests` | PASS，99 source files |
| `uv run python scripts/validate_workspace.py` | PASS，29 required files |
| `git diff --check` | PASS |
| Replay 五状态离屏 smoke | PASS，5/5 PNG |
| SQLite WAL/备份/回滚/迁移定向回归 | PASS，8 selected tests |
| Mac 定向文件（逐文件独立进程） | PASS：17 + 1 + 13 + 6 tests |

说明：把四个 Qt 定向文件放在同一个 pytest 进程时，当前宿主 Qt 在第二个文件末尾发生
原生 segmentation fault；四个文件逐个独立进程均通过，完整 `uv run pytest` 也通过。该
宿主级测试隔离问题没有改变 App 逻辑结果，已在交接的未完成项中如实记录。

## 本轮 App 构建与 smoke

- 构建命令：`uv run pyinstaller --noconfirm --clean --distpath dist/macos-v1 --workpath build/macos-v1 packaging/stockwatcher-macos.spec`
- 构建结果：本机 `arm64`，Bundle ID `com.kahlilhazel.stockwatcher`，
  `NSHighResolutionCapable=true`。
- `codesign --verify --deep --strict`：PASS（ad-hoc）。
- 包扫描：未发现 TdxQuant、TQ、PowerShell、VBS、Inno 或 Windows portable 入口。
- 直接冻结二进制启动 8 秒：进程保持运行，发送 SIGTERM 后退出；日志仅有被 spec 排除的
  非 macOS keyring 插件缺失警告，没有凭据或行情输出。
- 本轮没有启动交易时段实时扫描：2026-08-01 为周六；不会用旧缓存、Replay 或静态回顾
  冒充新鲜固定时点 Top3。

## 已确认与仍待现场验证

已确认的 Mac 能力沿用 `validation-20260731.md`：Keychain、路径、单实例、关闭隐藏、Dock
唤起、Retina/多屏、静态缓存、原生实时 1/100/300/800、最终修复后双次手动 Top3、板块硬门、
同板块最多 2 只、资金未确认不阻塞和 `.app` 构建。

本轮未新增下列交易日证据：

1. 新鲜 09:45 或 14:45 固定 Top3；
2. 交易日 15:30 准点报告和完整 30 天历史清理；
3. 真实睡眠/唤醒、断网/恢复的人工图形会话；
4. Primary 普通 Pro 静态路线的限流恢复（当前仍应如实标记 `rate_limited`）；
5. Windows 独立验收（仍为 FAIL，不能由 Mac 结果替代）。

唯一下一步仍是下一个交易日使用安装版执行 09:25–15:40 现场验收，记录固定时点、15:30、
恢复三轮和真实指标后再决定是否封版。
