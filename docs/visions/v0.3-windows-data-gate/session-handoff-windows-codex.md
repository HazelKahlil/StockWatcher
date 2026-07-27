# Windows Codex 直接交接

> 交接日期：2026-07-27
> GitHub：`https://github.com/HazelKahlil/StockWatcher`（private）
> GitHub 交接分支：`fix/HAZ-418-blockers`（单一 draft PR #2 的 head）
> 冻结源码 commit：`6e5dbed8eee027ef7d5478b18b1539b3c16a24ed`
> 直接 parent：`1d3f3e2915267466115208eae0e18b7ef380a234`

## 目标

Human Owner 脱离 Multica 后，在自己的普通用户 Windows 交互桌面把 HAZ-526 冻结候选完成真实 live readback；首次启动严格通过后，再在真实交易时段执行至少 30 分钟的官方 TdxQuant 单人只读 M0。事实以此仓库和现场产生的脱敏证据为准，不依赖 Multica 聊天记忆。

## 已完成

- 本地权威 `main` `6e193a3c20177220d89a7497004af281a7509270` 到冻结源码 commit 为 9 个提交的安全线性后继；包含 HAZ-511、HAZ-512、HAZ-515、HAZ-526。
- HAZ-511 为股票列表请求固定官方整数参数 `list_type: 0`；供应商 `ErrorId != 0` 继续 fail-closed。
- HAZ-515 将完整应用树、PySide6 UI、原生 Preflight 和严格 PASS 门纳入便携 ZIP。
- HAZ-526 删除 Preflight 失败后自动启动 `TdxW.exe` 的路径，并锁定 VBS/Python 启动链无 elevation verb。
- Mac 工程门、离线 Windows 包合同与 Replay smoke 已通过；冻结 ZIP、manifest、commit marker 和 38 个 payload 已在 HAZ-536 重新下载并复算。

## 冻结输入

| 项 | 锁定值 |
| --- | --- |
| ZIP | `StockWatcher-Internal-Portable.zip` |
| ZIP SHA-256 | `f0868ea1990ab3f6e0824810114bc8a41c5b328acda3554b26ea3ee316ba075a` |
| manifest | `MANIFEST.sha256` |
| manifest SHA-256 | `33cffbfd5415308df087f57baf10f8ed238f75e3c58bec4056c678fdd0fdd9d8` |
| source marker | `commit=6e5dbed8eee027ef7d5478b18b1539b3c16a24ed` |
| parent marker | `parent=1d3f3e2915267466115208eae0e18b7ef380a234` |
| archive/payload | 39 个 archive 成员，其中 38 个 payload；manifest 38 行 |
| payload 复核 | 路径集合一致，38/38 SHA-256 通过，0 mismatch |

附件原始来源是 HAZ-526；不要使用 HAZ-515 或更早的 ZIP，也不要复用旧解压目录、报告、截图或启动状态。

## 未完成

- HAZ-526 尚无普通用户 Explorer 真实双击、无 UAC、无新增 `TdxW.exe`、官方 Pythonw、Application Control、原生 Preflight、真实 UI、正常退出和再次启动证据。
- 真实官方 TdxQuant live readback 尚未完成。
- 真实交易时段至少 30 分钟 M0、全市场/板块/历史/性能/重连、紫黄线与授权结论尚未完成。
- 资金字段继续 `unavailable`；不得用 `Zjl`、`Zjl_HB` 或替代字段冒充紫黄线。

## HAZ-527 的准确边界

HAZ-527 没有验证到候选。Windows Computer Use 在 Explorer 枚举前反复报 `timed out after 15000ms connecting runner pipe-in`；后续最小只读命令也被同一宿主 runner 阻断。冻结附件没有下载、没有解压，候选没有启动；hash、Preflight/API/TQ、UI、UAC 和 Windows 设置均未触碰。

因此 HAZ-527 只能证明 Multica Windows runner 当时不可用，不能把 HAZ-526 判为失败，也不能判为通过。Human Owner 在本地 Windows 与 Codex 一对一继续时，应从 fresh clone/fetch、全新候选输入和全新中文/空格解压目录开始。

## 必读顺序

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/project/index.md`
4. `docs/process/index.md`
5. `docs/process/boundaries.md`
6. `docs/process/rules/data.md`
7. `docs/process/rules/security.md`
8. `docs/visions/README.md`
9. `docs/visions/v0.3-windows-data-gate/README.md`
10. 本文件
11. `docs/visions/v0.3-windows-data-gate/windows-handoff.md`
12. `docs/reference/v2.0/m0_checklist.md`

## 下一步

1. 在 Windows 对 private repo fresh clone，或对可信现有 clone fetch；先只读核对交接分支与冻结源码 commit。
2. 在源码树完成只读仓库检查和项目测试，不修改依赖、锁文件、系统策略或业务口径。
3. 从 HAZ-526 的锁定输入建立全新中文/空格解压目录，复算 ZIP、marker、manifest、路径安全和 38/38 payload。
4. 在已解锁的普通用户桌面，仅通过 Explorer 真实双击唯一入口 `启动 StockWatcher.vbs` 一次；首次失败立即停止。
5. 首次完整 PASS 后验证 UI、正常退出和同一入口再次启动；然后在真实交易时段执行至少 30 分钟 M0。
6. 若必须改代码，新建 Windows 工作分支、提交并 push；禁止 merge、tag、release。

## 风险与禁止项

- 只使用官方通达信/TdxQuant 与本机回环 TQ 服务；不接非官方行情服务器，不逆向，不用 OCR/鼠标脚本作为数据源。
- 不读取、索取、输入或回传 Windows/通达信密码、验证码、token、交易账号、持仓、订单或行情响应体；不连接券商交易或下单。
- 不接受或绕过 UAC，不关闭或绕过 Defender、防火墙、PowerShell 执行策略、Application Control、TLS/系统信任，不改系统 PATH、服务、注册表或计划任务。
- 首次 Preflight 失败不得修复后重跑来把失败刷成成功；保存首个真实失败点并停止。
- Mac/离线/较早 Windows 工程证据不能替代 HAZ-526 的目标 Windows live readback 或交易时段 M0。

## 验证状态

HAZ-536 在 macOS + Python 3.12.11 上对交接树执行：

- `uv sync --all-groups`：exit 0。
- `uv run pytest`：118 passed，20 skipped；跳过项来自当前宿主没有 `pwsh`。
- `uv run ruff check .`：exit 0。
- `uv run mypy src tests`：38 个源文件无问题。
- `python3 scripts/validate_workspace.py`：27 个必需文件通过。首次运行因本轮复核 ZIP 暂存于仓库内而按规则失败；将临时证据移出仓库后重跑通过，ZIP 未进入 Git。
- `python3 scripts/check_windows_package.py`：离线 Windows 包合同通过。
- `QT_QPA_PLATFORM=offscreen uv run python scripts/capture_mac_ui_evidence.py <全新临时目录>`：五状态 Replay PNG 5/5 生成。
- `git diff --check`：exit 0。
- HAZ-526 原始附件复核：ZIP/manifest SHA-256 精确匹配；单一安全根目录；39 个 archive 成员；manifest 38 行；路径集合一致；38/38 payload 哈希通过；commit/parent marker 精确匹配。

这些是 Mac 工程、离线合同、Replay 和产物机械证据，不构成 Windows 普通用户启动、UAC、官方 TdxQuant live readback、真实 UI 或 M0 证据。

## 可直接复制给 Windows Codex 的完整提示词

```text
你现在接手 StockWatcher 的 Windows 真实验证。不要依赖任何 Multica 聊天记忆；事实只以 private GitHub 仓库、下列精确 ref、workspace 文档和本机新产生的脱敏证据为准。

【锁定仓库与输入】
- GitHub：https://github.com/HazelKahlil/StockWatcher
- 交接分支：fix/HAZ-418-blockers
- 冻结源码 commit：6e5dbed8eee027ef7d5478b18b1539b3c16a24ed
- 直接 parent：1d3f3e2915267466115208eae0e18b7ef380a234
- ZIP：StockWatcher-Internal-Portable.zip
- ZIP SHA-256：f0868ea1990ab3f6e0824810114bc8a41c5b328acda3554b26ea3ee316ba075a
- manifest SHA-256：33cffbfd5415308df087f57baf10f8ed238f75e3c58bec4056c678fdd0fdd9d8
- archive：39 个成员，其中 38 个 payload；manifest 38 行；必须 38/38 哈希通过、0 mismatch

【先做仓库核验】
1. 使用 fresh clone；如必须复用已有 clone，先确认没有用户未提交工作，再 fetch。不要 reset、force-push 或覆盖用户文件。
2. 从 origin/fix/HAZ-418-blockers 核对冻结 commit 可达，然后 checkout 精确 commit 6e5dbed8eee027ef7d5478b18b1539b3c16a24ed（可用 detached HEAD 做验证）。
3. 立即回读并保存：
   - git status --short（必须为空）
   - git rev-parse HEAD（必须精确等于上述 commit）
   - git show -s --format="%H %P %s" HEAD（直接 parent 必须精确匹配）
4. 按顺序完整阅读：AGENTS.md → docs/README.md → docs/project/index.md → docs/process/index.md → docs/process/boundaries.md → docs/process/rules/data.md → docs/process/rules/security.md → docs/visions/README.md → docs/visions/v0.3-windows-data-gate/README.md → docs/visions/v0.3-windows-data-gate/session-handoff-windows-codex.md → docs/visions/v0.3-windows-data-gate/windows-handoff.md → docs/reference/v2.0/m0_checklist.md。若精确 commit 中缺少 session handoff 文件，则用 git show origin/fix/HAZ-418-blockers:docs/visions/v0.3-windows-data-gate/session-handoff-windows-codex.md 只读打开；不要因此切换到未核对的代码。
5. 先做只读仓库检查和项目测试，再做 Windows 现场操作。至少执行并保留完整输出：uv sync --all-groups、uv run pytest、uv run ruff check .、uv run mypy src tests、python scripts/validate_workspace.py、python scripts/check_windows_package.py、git diff --check。任何失败先停下，不能删测试、跳过检查、放宽断言或改锁文件掩盖。

【HAZ-527 边界】
HAZ-527 卡在 Windows Computer Use 的 runner pipe-in，发生在 Explorer 枚举和候选下载之前。候选没有下载、解压或启动；Preflight/API/TQ、UI、UAC 和 Windows 设置均未触碰。因此它不是候选 FAIL，也不是 PASS。你必须从全新输入和全新中文/空格目录开始，不复用旧报告、截图、解压目录或启动状态。

【冻结包机械核验】
1. 只使用上述 HAZ-526 冻结 ZIP、MANIFEST.sha256 和 SOURCE_COMMIT.txt。
2. 在名称含中文和空格的全新隔离目录中验证：ZIP SHA-256、commit/parent marker、单一安全根目录、无绝对路径/盘符/.. 穿越、archive 39 个成员、manifest 38 行、payload 路径集合一致、38/38 SHA-256 通过、0 mismatch。
3. 任一不匹配立即停止；不要修改 ZIP、manifest、marker 或包内文件。

【安全边界】
- 只使用官方通达信/TdxQuant 和本机回环服务 127.0.0.1:17709。
- 禁止读取、索取、输入或回传账号、密码、验证码、token、持仓、订单、交易信息或原始行情/HTTP body；禁止连接券商交易或下单。
- 禁止绕过或关闭 UAC、Defender、防火墙、PowerShell 执行策略、Application Control、TLS/系统信任；禁止改 PATH、注册表、服务、计划任务或安全策略。
- 不使用非官方行情服务器、逆向、pytdx、OCR 或鼠标脚本作为生产数据源。
- 现场出现需要账号/授权或安全策略决策时停止，请 Human Owner 自己通过官方正常界面处理；不要要求其把凭证交给你。

【Windows 普通用户 live readback】
1. 确认当前是已解锁的普通用户可见交互桌面，Explorer 可操作。记录启动前 TdxW.exe 进程集合与签名状态，只保留脱敏结论。
2. 官方终端/TQ 应由 Human Owner 通过官方正常入口启动并登录。StockWatcher 自有链不得自动新增 TdxW.exe；出现 UAC 立即停止，不点击、不接受、不绕过。
3. 可在双击前做一次最小只读会话，仅核对显式 {"market":"5","list_type":0}、HTTP/JSON 是否有效及 TQ ErrorId=0；不得输出响应体或行情。代码与现场调用都必须确认 list_type=0。
4. 只通过 Explorer 对唯一入口“启动 StockWatcher.vbs”发送恰好一次真实双击；不能用 shell、PowerShell、VBS/Python 命令行、session 0 或旧截图代替。
5. 机械核对：无 UAC；StockWatcher 没有新增 TdxW.exe；若终端/TQ 未就绪或 Preflight 失败，必须中文 fail-closed、不打开 UI。
6. Preflight 只有在报告整体 status=PASS、恰好一个 api_session=PASS、windows_live_verified=true，且固定检查集合完整无重复时才算通过；TQ ErrorId 必须为 0。
7. 首次任一门失败，立即停止并保存首个真实失败点。不得修复后重复启动或重复 Preflight 把失败刷成成功。
8. 只有首次完整 PASS 且真实 StockWatcher UI 可见后，才正常退出应用，再从同一 Explorer 入口做一次再次启动 smoke；准确记录第二次是否重新执行 Preflight，不能用第二次结果替代第一次。

【交易时段 M0】
live readback 完整成功后，按 docs/reference/v2.0/m0_checklist.md 在真实交易时段执行至少 30 分钟单人只读 M0。覆盖全 A 股列表/价量、至少三日历史、板块/成分、交易日历、新鲜度、p50/p95、错误率、断线与恢复；至少 3 只股票每 5 秒比对界面与程序值，连续不少于 30 分钟。数据 STOPPED/RED 时不得产生新候选，恢复必须先预热。紫黄线、Level-2、Zjl/Zjl_HB 和授权口径未被官方现场证据证明前保持 unavailable，不得替代或冒充。结论只能是 PASS、PASS_WITH_LIMITS 或 FAIL。

【若必须改代码】
验证优先，不要现场随手改。如果确需修复：从冻结 commit 新建含 Windows 验证语义的工作分支，先记录首个失败证据，再做最小改动；完整运行测试，commit 并 push。禁止 merge、force-push、tag、release、deploy，也禁止改锁定业务项、供应商口径、依赖或系统安全设置来过门。

【回交格式】
最终一次性给出：
- 仓库 URL、Windows 工作分支、精确 commit、parent、git status；
- git diff --stat / git diff --name-only（若无代码改动明确写无）；
- 每条测试命令、exit code、pytest passed/skipped 数；
- ZIP/manifest/marker/archive/payload 复算结果；
- 普通用户交互会话、Explorer 恰好一次首次双击、UAC、新增 TdxW、list_type=0、TQ ErrorId、Preflight 三项严格门、UI、正常退出、再次启动的脱敏 readback；
- M0 的时间范围、持续分钟、覆盖、p50/p95、错误率、恢复行为、PASS/PASS_WITH_LIMITS/FAIL；
- 日志、报告、截图的本机路径与 SHA-256（只报脱敏产物；不要上传或粘贴 secret、用户名、主机名、账号、绝对用户目录、行情或 HTTP body）；
- 仍未验证项和唯一下一步。

第一步先只读回报 git status、HEAD/parent、必读文件是否齐全；确认后继续。不要跳过门禁，不要把 Mac/离线/旧 Windows 证据写成这台 Windows 的 live PASS。
```
