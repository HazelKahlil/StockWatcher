# PR #12 合并准备与本地验证交接

日期：2026-09-11。

Human Owner 本轮明确授权：处理确认存在的问题，验证后合并 PR #12，再由 Owner 本地验证。授权目标保持 `codex/ui-motion-performance`，不是 `main`；没有生产部署、真实行情采集或交易账户操作授权。

## 已完成 UI 不再重做

UI 实现继续对应 `029a90d3e2f7dc9846184c559d77e7fb9adfa255`。第三轮截图和测量保留在 `31924d2141d5352201fb9d8fb9d5e2bc4196b445`，测试收尾基准为 `fa94920f9620b94839b84c78f5bc9e4bb8352bcc`。本轮合并准备没有更改页面、卡片、排名或显示密度实现。

## 仓库级检查修复

1. 空白检查使用完整 Git 历史，按 PR 实际 merge-base 到 head 的差异执行 `git diff --check`。不再用浅克隆的合成 merge 提交把历史审计文件当作全部新增内容；没有关闭空白检查或排除本 PR 新增文件。
2. 重新逐项读取已有六个安全证据匹配，将 `test_web_auth_api.py` 两个移动后的隔离测试 fixture 定位从 54/55 改为 71/72。明确这只是既有发现的重新核验，不是新的全仓库秘密扫描。
3. Windows 的快照 fsync 使用 `r+b` 打开已存在的暂存快照，满足 Windows 可写句柄要求而不截断内容；POSIX 保持 `rb`。继续调用 fsync，错误继续上抛，不更改 Schema、租约检查或实时数据库读写规则。新增平台模式、真实 fsync、失败上抛、缺失文件和租约 fencing 回归。
4. CNB escrow 执行测试包含 Bash 和 POSIX 文件权限语义，只在 POSIX 执行，同时加入 Linux Web 必跑测试；其静态合同测试仍跨平台执行。没有跳过 SQLite 备份或恢复测试。
5. 依赖审查先核对实际 diff 中的依赖声明、锁文件、镜像声明和 Action 引用变化。没有依赖差异时明确记为差异审查不适用，不声称现有依赖不存在漏洞。有变化时仍必须通过原 dependency-review-action，不忽略服务不可用或漏洞失败。仓库 Dependency graph 服务设置没有被修改。

整文件 API 编辑后的 diff 复核纠正了中间提交中的意外文本/租约检查变化；最终 `sqlite.py` 相对合并前基准的差异只限于 `_fsync_file` 的句柄模式和解释注释。新增租约回归也用于确认此边界。

## 验证与合并记录

实际结果以 PR 当前 head 对应的 Governance 作业与合并记录为准。本文不预先宣称 CI 全绿或已合并。最终结果在 PR conversation 中补充，包括精确 head、运行编号、各作业状态和合并 SHA。

必须保留 `STOCKWATCHER_REQUIRE_UI=1` 与 `-W error`。不将 Linux 模拟测试、构建成功或无依赖差异写成真实行情、Windows/通达信现场或生产验收。

## Owner 本地验证

建议从合并提交新建独立 worktree，避免覆盖现有改动或触碰当前生产数据目录。先执行锁定依赖安装和 UI 回归：

```bash
uv sync --all-groups --frozen
uv run playwright install chromium
STOCKWATCHER_REQUIRE_UI=1 uv run pytest tests/test_web_display_layout.py -W error
node --test tests/test_ui_display.mjs
```

浏览器检查可先使用仓库 `evidence/web-display-density/preview-dashboard.html` 模拟预览，确认 100% 窄窗口、20%/35% 紧凑模式、恢复默认和详情相关正式页面回归。静态预览不等同于真实接口或登录验收。

后续真实应用验证继续使用项目既有隔离环境，不启动生产采集或复用现网数据库。Owner 确认后，生产发布仍作为独立、另行授权的步骤。
