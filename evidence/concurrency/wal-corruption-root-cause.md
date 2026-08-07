# SQLite WAL 并发损坏：根因与修复（2026-08-07）

## 现象

web + worker 双进程并发写入同一 SQLite WAL 数据库时，Linux 容器（任意存储层：
Docker 命名卷、bind mount、容器内 tmpfs）约 20 秒内出现物理页混写——
`automation_tasks` 的行数据精确落入 `web_audit_log` 的 b-tree 页。
`PRAGMA integrity_check` 报 `NUMERIC value in web_audit_log.object_id` 或
`wrong # of entries in index sqlite_autoindex_web_users_1`，损坏目标每次不同。

## 排除项（均有实验证据）

- Docker Desktop 存储层假象：容器内 tmpfs（真实 Linux 内存文件系统）同样复现；
- `PRAGMA journal_mode=WAL` 每次连接执行：改为每进程一次后仍复现；
- 每调用 `PRAGMA integrity_check`（initialize）：全量缓存后仍复现；
- 每连接 `CREATE TABLE IF NOT EXISTS schema_version`：改为只读探测后仍复现；
- 裸 sqlite3 双进程压力（2.2M 行、BEGIN IMMEDIATE + integrity_check + 高频开合）：
  不损坏——差异不在 SQL 语义。

## 根因

SQLiteStore 原先每个 store 调用都新开连接并在调用后关闭。两个进程在任意时刻
都可能出现“最后一个连接关闭”的瞬间；SQLite 此时删除/重建共享的 `-shm`/`-wal`
文件。持有陈旧视图的进程随后与重建文件的新进程之间丢失互斥锁，
两个 writer 同时写同一文件的不同页视图 → b-tree 页混写。

追踪实验佐证：开启 `STOCKWATCHER_SQL_TRACE=1`（每条 SQL 打印，显著降低连接频率）
后同一场景不再损坏；恢复高频后立刻复现。

## 修复

`SQLiteStore.connect()` 对读写 store 返回**每线程常驻连接**（thread-local，
进程生命周期内不关闭）；`-shm`/`-wal` 因此永不被删除，跨进程写锁始终有效。
只读 store 保持临时连接。

## 验证

- 修复前：双进程 Linux 容器 20s 内损坏（多次复现）。
- 修复后：容器 tmpfs 双进程（web+worker）90 秒连续登录 + worker tick，
  `integrity_check = ok`，audit 10 行 / automation 3 行内容正确；
  之后多次重启栈复测均稳定。
- 原生 macOS 双进程 200 次并发登录：`integrity_check = ok`（修复前后均通过）。
- 全量 391 项测试、ruff、mypy strict 全绿。

## 生产含义

目标 VPS 为 Linux 原生文件系统；本修复消除了并发 WAL 写损坏风险。
部署前仍必须按 `13-Test-and-Acceptance-Plan.md` 做完整交易日 Live 验收。
