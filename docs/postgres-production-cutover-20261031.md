# PostgreSQL 生产切换实录（2026-10-31）

## 最终结果

2026-10-31 12:26:00 UTC+8（04:26:00Z）已提交真实生产切换。`127.0.0.1:8091/v1/health` 返回 HTTP 200、`{"ok":true,"database":"postgresql","agent":false}`。管理员真实密码登录成功；真实 QQ bot `7356b371-1be2-4633-adbd-096f05554e5f` online，AI ready。运行库为 `fishbot_cutover_20261031c`，全新运行卷为 `fishbot-production-runtime-c`。

API、Agent 为独立容器；gate 提供共享 network/PID namespace 和固定外部 8091。反向 WS 继续由 AstrBot 子进程监听，不新增 WS 代理。API 配置 `EXTERNAL_AGENT=true`、`DB_AUTO_MIGRATE=false`、runtime 角色；runtime 实测 `rolsuper=false, rolcreatedb=false, rolcreaterole=false`。migrationowner 仅用于迁移和维护。

## 窗口、故障与修复

- 第一次窗口 04:16:06Z 开始；停旧、备份后导入事务内 hash 校验失败，04:16:54Z 启动旧容器回滚，旧服务恢复 healthy。新 QQ owner 未启动。
- 根因：复合主键表 `bot_config_revisions` 使用 `ORDER BY 1`，同 bot 多条 revision 的返回顺序不稳定。诊断证明整行集合完全一致，仅顺序不同。`packages/database/transfer.ts` 改为规范化整行排序后计算 hash，仍覆盖所有字段与重复行；没有绕过校验。
- 第二次窗口 04:19:51Z 开始；约 04:19:53Z 旧容器已停，备份恢复至新卷；04:20:38Z 完成导入与独立 dump/restore 验证，之后才启动新 QQ owner。
- QQ 进程等待登录且 quick-login 列表为空。恢复原容器 hostname `537be6317068` 后，原有保存会话立即恢复 online；未重新扫码、未生成替代 key。Agent 在 04:23:56Z 受控重启一次应用该身份修复，随后才做正式 API 重启验收。
- 原验证脚本误把管理端口当作 OneBot HTTP 端口；改为读取 manifest 的 `management.port+1`。轮询也曾触发正常登录限流，调整为 QQ/AI ready 后才登录，没有降低限流配置。
- 04:25:19Z API 单独重启，04:26:00Z 验收完成并提交、开放 gate。QQ 受控窗口约 6 分钟，包含会话身份定位。

## 一致性备份和迁移验证

最新完整备份在旧 owner 停止后生成，旧原卷未覆盖。从完整备份恢复到全新卷，再通过正常 Agent lease 导出 PGlite；未绕过 live lease。

远端根目录：`/opt/fishbot-pg-cutover-20261031/`。

| 产物 | SHA-256 |
| --- | --- |
| `final-artifacts-slim.tar.gz` | `e994e293f5285b0f8f57c65c64a0b53da763fc6d5132d3dc7b79a8d64cfd17b4` |
| 追加修复的 `app-final-b/dist-server/packages/database/transfer.js` | `379dd659ecb4d6e5def36683f479ae2145e4cace0e2c8d614f551720f3761346` |
| `consistent-data-final-20261031-121953.tar` | `934eee475e97312dc28bc5c531ef943a4f120daccfe0d05dbb9c5543e9591a5d` |
| `final-fresh-pg.dump` | `d576795bfec204eab02e800a1ed3e93fd4132b865a2f352624268e89b270b6b3` |

新 app 为 `app-final-b`，先复制旧 app，再展开最终包，将 `apps-web-dist` 合并到 `apps/web/dist`，保留旧 design-exploration/illustrations 等未打包资源。

导出 snapshot SHA 为 `72178dbe6a8ec19629e7000e90cea72ecf609421bb29c4c6e47c063df9cf7f37`；16 表规范化 content SHA 为 `c32f81d7e721a20f28bc9319e56d9060c98601a6d0812b4f1dee8cf77686002c`。原库与 `restore_verification_final_c` 均逐表和全量 hash 校验成功，3 份 ciphertext 用原 master.key 解密成功；原 sequence `49 / is_called=true`，目标导入 sequence `50 / is_called=false`，恢复库相同。旧 `fishbot`、`rollback_verification` 等数据库保留。

| 表 | 迁移行数 |
| --- | ---: |
| users | 9 |
| tenants | 9 |
| memberships | 9 |
| sessions | 40 |
| runtime_groups | 1 |
| bots | 10 |
| bot_config_revisions | 43 |
| secrets | 3 |
| instances | 10 |
| qq_bindings | 3 |
| login_sessions | 39 |
| commands | 131 |
| invitations | 6 |
| invitation_redemptions | 8 |
| group_members | 3 |
| audit_events | 49 |

这些是迁移边界行数；后续登录、Agent 恢复及真实业务允许正常写入。

## API 重启证据

`onebot-samples-c.jsonl`：在 API 重启前、中、后连续采样 30 次，全部真实 `/get_status` online，0 失败。`control-final-c/before-api-restart.json` 与 `runtime-current.json` 的 process records 完全相同：

- NapCat PID `1874`，created `1793420643.71`，started `04:24:04.639Z`。
- 共享 AI PID `1809`，created `1793420636.69`，started `04:23:57.621Z`。
- Agent StartedAt 保持 `2026-10-31T04:23:56.159998748Z`；API 新 StartedAt 为 `04:25:19.852077652Z`。

只向 owner `768687099` 发送一条验收文字，OneBot retcode 0、message_id `719354624`，04:27:02Z。未触碰签名服务或他人账号。

## 回滚与写入边界

独立 host systemd deadman timer 在停旧前已 active，18 分钟无提交会停止全部新 owner 后启动旧容器，恢复记录的 memory.high 并启动旧 controller/guard；第一次失败真实执行了这一回滚路径。第二次提交使用 commit 文件锁与 COMMITTED 标志，timer 已停止。提交后回滚程序直接退出，禁止自动回 PGlite 遗失新写入。

所有旧容器/卷/镜像/备份均保留；第一次新卷 b 与失败事务证据也保留。提交前 gate 关闭，应用进程仍可能写 PG/SQLite；若提交前失败，会完整保留新 PG 和运行卷，必须人工比对新旧库/SQLite并同步后才能丢弃，不能将旧库当作这些新增写入的替代副本。第一次窗口新 owner 未启动；第二次已提交，无此回退。生产后如需故障恢复，应以当前 PG/运行卷备份恢复为基线，不能直接启动旧 PGlite owner。

## 最终服务状态与接手

截至 04:28Z：

| 服务 | 状态 |
| --- | --- |
| fishbot-production-api | running / healthy，unless-stopped |
| fishbot-production-agent | running / healthy，unless-stopped |
| fishbot-production-pg | running / healthy，PostgreSQL 17.6，unless-stopped |
| fishbot-production-gate | running，8091 HTTP 200，unless-stopped |
| fishbot-current-platform | stopped，restart=no，原配置另存 old-container.json |
| fishbot-snapshot-api / agent / pg | stopped，restart=no，全部保留 |
| fishbot-migration-drill-pg | stopped，restart=no，保留 |
| fishbot-memory-controller / guard | inactive、disabled，源文件未修改 |
| 两次 cutover deadman timers | inactive |
| fishbot-qq-hostname.timer | active、enabled |

新运行容器 cgroup memory.high 均为 `max`，旧 registered cgroup 已 retired。主 agent 负责新内存 controller，禁止启动指向旧容器的旧 guard/controller。

新增 `preserve-qq-hostname.py` 与 `fishbot-qq-hostname.service/.timer`：每 10 秒检查 gate UTS hostname，仅发生 namespace hostname 漂移时停止 Agent、恢复原 hostname、重启 Agent；不停止 API。当前检查无需动作，正常 API 重启不改变 Agent。该机制保障 Docker/host 重启后的最终恢复，但未做整机或 gate 重启演练；可能存在启动后至 timer 修复前的短暂登录失败。未来重建 gate 应直接用原 hostname 创建，避免依赖补偿。不要随意修改此 hostname。

主 agent 还需接手：新内存控制器；常规 PG 自动备份与保留策略；整机/gate 重启演练。已保留 `app-final-b` 中原模块 `transfer.js.before-order-fix`；最终部署基于上述 archive 加单模块补丁，不能仅用原 archive 重建而遗漏该修复。

## 验证与 Plan vs Actual

- `tsc --noEmit` 通过。
- `node --import tsx --test --test-concurrency=1 tests/database-migration.test.ts`：3/3 通过，覆盖原 key 保留、损坏快照拒绝、lease 拒绝、bigint 一致性、复合键行顺序不变性和重复行/值修改检测。
- 隔离最新 Agent 心跳正反例通过：真实 PID1 接受、过期业务 tick/错误 processStart/过期 lease 拒绝。原隔离 unhealthy 主因缺少 docker-health.mjs；补入最终 scripts 后验证，隔离容器最终停止。
- 16 表、hash、3 key 解密、sequence、dump/restore、真实 QQ、AI ready、管理员登录、API 重启连续采样均完成。
- 计划偏移：b 库/卷失败后换 c 库/卷，不复用污染目标；补充 hash 行序修复、原生 hostname 保护及验证端口修正。均由实际失败证据驱动，未越过 API/Agent 职责边界。
- 未新增或修改内存控制器/guard 源文件，未执行任何 Git 操作。没有声称整机重启或模型实际推理已通过；AI 验收为业务 ready。

主要远端证据：`final-execution-c.log`、`final-status.json`、`final-process-evidence.json`、`control-final-c/fresh.json`、`control-final-c/before-api-restart.json`、`control-final-c/runtime-current.json`、`onebot-samples-c.jsonl`、`onebot-samples-done-c.json`、`control-final-c/owner-message.json`、`cutover-final-c.py`、`rollback-final-c.py`、`COMMITTED`。受保护证据可能包含会话或密文，勿公开原文。
