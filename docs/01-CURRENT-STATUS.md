# 当前状态

> 核验环境：macOS 27.0 arm64；日期：2026-08-07。Mac 证据不能外推 Windows 或 Linux VPS。

| 轨道 | 权威代码基线/分支 | 已核验 | 未完成/阻塞 | 结论 |
| --- | --- | --- | --- | --- |
| Shared Core | `main` 应用代码基线 `88ccf49f91fa814af83a004232315286feca3fb7` | Provider/Transport、全市场扫描、股票池/缓存、行业/概念、1/3/5 分钟、CandidateEngine、StableTop3、强异动、09:45/14:45、历史/总结、Selection Audit、SQLite/迁移 | 新鲜固定时点、15:30 准点、冷启动/睡眠/网络恢复仍需真实窗口补验 | `accepted`，Mac 内部试用基线 |
| App Mac | 安装版 `SOURCE_COMMIT`=`88ccf49f...` | arm64 Mach-O；ad-hoc `codesign --verify --deep --strict`；Keychain、SQLite、PDF、单实例、窗口恢复、关闭隐藏已在本机实测 | 新鲜固定时点、15:30 准点、真实睡眠/断网恢复 | `internal_trial` |
| Web 内部测试 | `web/internal-test-v1` / `87a8b85609f57504861e09f416694582556b736e`；基线 `502a447d...` | 391 passed、20 skipped、2 deselected；Ruff/Mypy pass；Compose config pass；macOS Docker/browser 证据存在 | 当前 workspace validator 被 `.venv` Playwright 示例坏链阻断；VPS preflight、域名/TLS、真实数据、完整交易日验收 pending | `blocked / not_accepted`，不合 main |
| Windows | 未来 `windows/internal-test-v1` | 历史工程/打包与交接材料已归档；共享主线已包含历史 Windows 能力 | 真实 M0、交易时段、通知、多屏、安装/回滚未完成；历史结论仍 `FAIL` | `planned` |

## 当前禁止的误读

- Web 的离线 pytest、容器、浏览器证据只证明 macOS Docker Desktop 行为，不证明 Linux VPS 或真实行情。
- Mac 的实时 Top3、通知、安装和性能证据不证明 Windows/通达信。
- `blocked` 不因为文件存在、镜像 digest 存在或单元测试通过而改成 `accepted`。
