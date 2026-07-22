# v0.0-project-bootstrap：建立可继承的项目事实源

> 状态：已封版
> 创建：2026-07-22 ｜ 封版：2026-07-22 ｜ Tag：`v0.0.0`

## 目标与范围

- 创建私有 GitHub 仓库 `HazelKahlil/StockWatcher` 与本地权威目录。
- 安装项目工作流、领域规则、版本与发布治理。
- 从交接包中选择机器可读 MD 版本，迁入必要的锁定项、规格、M0/验收、配置、Schema 和图片。
- 不在本版实现任何产品业务逻辑；v0.1 M0 另行启动。

## 验收标准

- [x] GitHub private 仓库存在，默认分支为 `main`，fresh clone 可见全部治理与交接材料。
- [x] 本地目录位于 `20-Projects/StockWatcher`，并配置 `origin`。
- [x] `AGENTS.md`、文档地图、长期事实、过程规则、边界、版本索引和领域规则齐全。
- [x] V2.0 MD、锁定项、M0、验收、配置、Schema 与 4 张图入库；重复 DOCX/ZIP 未入库且取舍可追溯。
- [x] `python3 scripts/validate_workspace.py` 与 `git diff --check` 通过。
- [x] GitHub Actions `Governance` 首次运行通过。

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-07-22 | 完成仓库 Bootstrap、材料迁移、规则/版本骨架与自动完整性检查 | 已完成 |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| 仓库 private | 原规格限定内部 2—3 人使用，先采用最小暴露 |
| MD 为入库规格 | 可检索、可 diff；独立与包内 MD 哈希一致，Word/ZIP 原件保留在 Downloads |
| 原始图片路径被规范化 | `/mnt/data/` 是生成环境路径；只改为仓库内相对路径，自动检查防回归 |
| 本版无 PR | 新仓库需要先建立默认分支；初始 Bootstrap 直接创建 `main`，后续改动必须走 PR |

## Session Handoff 索引

无需单独 handoff；项目状态由本 README、`docs/visions/README.md` 与 `docs/reference/v2.0/` 承接。

## 封版记录

- 验证结果：本地完整性、Git whitespace、远端默认分支与 GitHub Actions 均通过。
- 遗留问题：产品实现未开始；由 `v0.1-m0-data-gate` 承接。
- 终态对账：关联 issue `HAZ-383`；必需子任务/stage `0/0`；Issue 状态与本 README 状态一致：是（等待 issue review）。
