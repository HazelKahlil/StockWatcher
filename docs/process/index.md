# 过程规则与踩坑索引

> 最后更新：2026-07-23
> 每条规则必须有“为什么”；后续 session 先从路由表定位必读规则。

## 硬边界

见 [boundaries.md](boundaries.md)。自动交易、凭证、未验证数据口径、生产数据迁移和锁定业务项都属于高风险区。

## 规则路由表（动手前查这里）

| 任务类型 | 必读规则文件 |
| --- | --- |
| 数据源、供应商字段、行情、板块、紫黄线、M0 | `rules/data.md`、`boundaries.md` |
| 股票池、评分、排名、强中近、提醒状态机、回放 | `rules/ranking.md`、`rules/data.md` |
| 主窗口、弹窗、托盘、声音、iPhone 通知 | `rules/ui.md`、`rules/security.md` |
| SQLite、缓存、Schema、备份、保留和导出 | `rules/storage.md`、`rules/security.md` |
| 运行日志、脱敏、滚动和保留 | `rules/storage.md`、`rules/security.md` |
| 新增或升级 Python 依赖 | `rules/security.md`、`dependencies.md` |
| 账户、凭证、网络、第三方授权、LLM | `rules/security.md`、`boundaries.md` |
| 版本、Changelog、tag、封版 | `release.md`、目标版本 README |
| 仅治理/文档改动 | `AGENTS.md`、本文件、目标版本 README |

## 已确立的规则

| 规则 | 为什么（出处） |
| --- | --- |
| 实时主链路必须确定性且可回放 | V2.0 锁定边界：不能让 LLM 猜股票，也要能复现每次提醒 |
| M0 是完整资金模块的开工门 | 紫黄线字段、授权和性能尚未现场证明，错误口径会让整个排名失真 |
| 健康为 RED/STOPPED 时不产生新候选 | 旧数据伪装正常结果比暂时无结果风险更高 |
| 规格变更必须版本化，不静默改锁定项 | 交接包明确了决策优先级，跨 session 必须可追溯 |
| local-first 期间，日常事实以本地 `main` 为生效终点 | 用户当前只有 Mac，频繁 GitHub 操作会增加摩擦；本地默认分支仍能给 Agent 提供稳定事实源 |
| GitHub 只在版本节点同步，且必须明确本地/远端差异 | 既降低日常操作成本，又避免把未同步本地进展误报成远端交付 |
| 验证证据必须标运行环境 | Mac 回放和 UI 证据不能证明 Windows/通达信行为 |
| v0.3 只推进 Windows + 官方 TdxQuant | Human Owner 已回到原规格正式路线；Mac/Tushare/iFinD 调研保留为历史证据但不再执行 |
| TQ HTTP 只允许官方本机回环服务 | `http://127.0.0.1:17709/` 是官方本机桥，不是供应商托管 API；禁止开放到局域网或公网 |
| TdxQuant 缺精确 `source_ts` 时只可 WARMING | 接收时间不能冒充供应商源时间；未通过现场时序 M0 前不得放行候选 |

## 踩坑记录

| 日期 | 坑 | 根因 | 结论 |
| --- | --- | --- | --- |
| 2026-07-22 | 原始 Markdown 的 4 张图引用运行时 `/mnt/data/` 绝对路径 | 交接文件来自一次性生成环境 | 入库时改为同目录相对路径，并用自动检查阻止绝对路径回归 |

## 决策记录

| 日期 | 决策 | 备选 | 理由 | 风险/应对 |
| --- | --- | --- | --- | --- |
| 2026-07-22 | GitHub 仓库设为 private | public | 规格限定团队内部使用，先采取最小暴露 | 如需公开，由 owner 单独审查授权、数据和文档后调整 |
| 2026-07-22 | 选择 MD 作为仓库规格基线，不提交重复 DOCX/ZIP | 同时提交 Word 与压缩包 | MD 与包内/独立版本哈希一致、可检索、可 diff；二进制重复会放大仓库 | 源哈希和省略项记录在 reference README，Downloads 原件保留 |
| 2026-07-22 | Bootstrap 作为 `v0.0.0`，产品实现从软件 v0.1 开始 | 直接称 V2.0 产品已开发 | V2.0 是交接文档版本，不是软件已交付版本 | 版本索引明确映射，避免把规格版本误当发布版本 |
| 2026-07-22 | 开发改为 Mac 本地优先，GitHub 在版本节点同步 | 继续每次改动走 GitHub PR | 当前无 Windows，且用户明确认为 GitHub 日常操作成本过高 | 本地 `main` 保持可恢复；版本节点用 `publish/<version>` + 单一 PR 做远端备份 |
| 2026-07-22 | v0.1 先做 Mac Replay 基础，真实 Windows/通达信 M0 延后到 v0.3 | 没有 Windows 仍强行执行 M0 | Mock/Replay、接口和确定性测试可以在 Mac 可靠完成，紫黄线与 Windows 行为不能 | 所有环境证据明确标注；v0.3 未通过前资金模块保持 unavailable |
| 2026-07-23 | Human Owner 批准 v0.3 改为双路线真实数据闸门：Mac 验证北京沃远数据科技有限公司 Tushare Pro，Windows 保留官方 TdxQuant | 用 Mac 路线替代 Windows；继续只等待 Windows | Tushare Pro 是首个 Mac M0 候选，TdxQuant 仍承接原规格现场证据；两者复用共享核心但互不冒充 | HAZ-403 保持 `PASS_WITH_LIMITS` 和 `NO-GO for implementation`；Tushare 仅走供应商正式支持的 HTTPS POST/JSON，授权账号归 Human Owner，token 只从 `TUSHARE_TOKEN` 读取且不落 issue、评论、日志、Git、SQLite、截图或示例配置；Agent 不注册、不购买、不接受条款、不回显 token |
| 2026-07-23 | Human Owner 将 v0.3 执行路线收敛为 Windows + 官方 TdxQuant 单人只读测试，上一行双路线决策被取代 | 购买 Mac 数据源；继续双路线 | 免费“金融终端（量化模拟）”与官方 TQ 本机接口能覆盖前置 M0，且不需要交易账户 | Mac 继续承担 Mock/Replay；Windows 真机、授权、紫黄线、性能与安装体验仍必须现场验证 |

## 维护节奏

- 每周或每个版本开工前检查活跃版本、待回填文档、边界和规则路由是否仍与代码一致。
- 每 1—2 个版本裁剪未执行的规则；保留的规则必须能检查。
- 巡检先核对上一次是否收口，不留下静默 `in_progress` 任务。
