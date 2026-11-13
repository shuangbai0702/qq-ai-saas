# PostgreSQL 真实连接预算交错验证

2026-11-13 实际执行 **7/7 通过**。`result.json` 是脱敏结果，包含当时执行源码与 bundle/migrations 的 SHA-256。没有数据库地址、口令、真实联系人或生产业务采样。

每个场景由独立 PostgreSQL backend PID 执行两个事务。首事务通过可控 barrier 持锁；通过 `pg_catalog.pg_stat_activity` 确认次事务实际处于 `wait_event_type='Lock'` 后才释放。覆盖：

1. 主请求先获得 cycle 锁，主/媒体争一个调用槽。
2. 媒体先获得 cycle 锁，主/媒体争一个调用槽。
3. 调用槽足够时主/媒体争同 cycle Token。
4. cycle finish 持锁，媒体等待后合法晚预留。
5. 媒体预留持锁，cycle finish 等待后结算。
6. 不同 finished cycle 争原日 `used_tokens + reserved_tokens` 容量，失败方完整回滚。
7. 同 Goal revision CAS 仅一个更新成功，并拒绝跨 scope 写入。

复现需要明确授权的 PostgreSQL 测试连接环境：`DATABASE_URL`，或项目已有 `PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD_FILE` 配置。程序复用 `configuredDatabaseUrl()`，不打印秘密。角色需有创建 schema、访问自身连接活动和删除自身 schema 的权限。不要绕过权限或把连接参数写进版本库。

在仓库根目录运行：

```powershell
node experiments/b-goal-budget-postgres-20261113/build.mjs
node .local/pg-budget-probe-repro/probe.mjs
```

构建输出在 `.local/pg-budget-probe-repro`，可以用 `FISHBOT_PG_PROBE_OUTPUT` 指定仓库内其他临时目录。部署到授权测试主机时携带该输出目录，放在能够解析仓库 Node 依赖的位置。不要提交生成 bundle 或重复的 migrations。

程序创建随机 `fishbot_budget_probe_<32hex>` schema，所有应用连接的 `search_path` **只包含该 schema**，使用当前 001–012 迁移构建独立表。所有 fixture 使用随机合成 tenant/bot；未读写生产业务表、未启停服务、未发送外部操作、未调用付费模型。最后关闭应用连接，核对 schema 精确名称及当前角色所有权，再 `DROP SCHEMA ... CASCADE` 并复查不存在。此次运行已确认清理成功；若进程被操作系统强杀，管理员需按输出的精确随机 schema 名核对所有权后清理，不能通配删除。

原执行 bundle 指纹为 `26291bedd989b414e4348b3aa907d02a52fd3d435a1e8ad38e467c01ff8c3fa8`。原 `.local` 入口迁入本目录后未重复远程执行；相对导入仍为 `../../packages`，构建输出路径改成根据脚本位置解析。源码布局和 bundle 字节变化会产生新指纹，新构建的 manifest 记录实际对应关系，不能把旧结果冒充新构建的执行结果。
