# V2.0 交接基线

本目录保存 2026-07-22“A股候选观察与异动提醒工具”最终开发交接包中，StockWatcher 后续实现真正需要的机器可读材料。

## 阅读顺序

1. `requirements.lock.json`：不可由实现者自行修改的业务锁定项。
2. `SPEC_V2.0_AGENT.md`：完整机器可读规格与冲突处理。
3. `m0_checklist.md`：正式开发前的数据可行性闸门。
4. `config.example.yaml`：交接时的工程默认值；仍需验证和版本化。
5. `schema.sql`：SQLite 初始样例；不是已经运行的 migration。
6. `acceptance_tests.md`：核心 UAT 清单。

发生冲突时：`requirements.lock.json` 的锁定业务项 > `SPEC_V2.0_AGENT.md` 的“决策与冲突处理” > 版本 README 中明确且已确认的增量决策 > 工程默认配置。任何新业务变化必须形成新版本，不能静默覆盖。

## 导入取舍

- 选择 Markdown 规格入库，因为它可检索、可 diff，并与独立下载的同名 MD 内容完全一致。
- 未提交 `FINAL_SPEC_V2.0.docx`、独立 DOCX 和交接 ZIP：它们是同一内容的排版/打包副本，会给 Git 增加不可读的二进制重复。
- 原始 Downloads 文件未删除，仍可用于排版核对或重新导入。
- `SPEC_V2.0_AGENT.md` 只做了 4 处迁移适配：把一次性环境中的 `/mnt/data/final_agent_package/assets/media/imageN.png` 改为本目录相对路径 `assets/media/imageN.png`。正文业务内容未改。

## 源文件校验

| 源文件 | SHA-256 | 入库情况 |
| --- | --- | --- |
| `SPEC_V2.0_AGENT.md` / 独立 MD | `9c0005f28f5b0f67eb5ae1ce49ade80ea51e69ae6b8096302d3fd007da75a918` | 已入库；仅规范化 4 个图片路径 |
| `FINAL_SPEC_V2.0.docx` / 独立 DOCX | `1a738128fa3e66f5f7225cd47abcbf02a6f657e0a09b8dada20fd82a4229c069` | 未入库；原件保留 |

包内 MD 与独立 MD 哈希一致；包内 DOCX 与独立 DOCX 哈希一致。这是选择单一 MD 基线而不保存重复二进制的依据。

## 开发硬约束摘要

- 第一件产品工作是 M0，验证通达信最新正式版能否合法、稳定、实时、批量读取全市场、板块与紫黄线数据并和界面一致。
- M0 未通过时，不得伪造或声称已经实现“通达信紫黄线”。
- 实时选股主链路必须确定、可复现，不依赖 LLM。
- 只做候选观察和异动提醒；不读取交易密码、不连接交易账户、不自动下单。
- 数据健康为 RED/STOPPED 时停止产生新候选，不用旧数据伪装正常结果。
