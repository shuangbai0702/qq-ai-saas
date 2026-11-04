# 内外层任务来源、经历与搜索复用修复

执行依据：用户在《B站请求优化与内外层协作核查》及三项解释后授权“你修复一下吧”。2026-11-04 实施。未执行 Git 操作，未覆盖前端。

## 修改结果

### 可信任务来源

`packages/database/autonomous.ts::commitDecision` 在保存目标、动作的同一个事务中返回被接受目标的 source；`apps/node-agent/src/autonomous.ts` 直接使用该返回值派发任务，不再从提交前的 goals 快照查找。

模型不能自行填写 source。新建委托目标仍需匹配后端观察到的真实消息；既有租户、目标状态、配置代次和工作租约校验保持不变。同一轮创建目标后立即执行，来源群现在能够传给内层。

### 经历构建与会话归属

`adapters/astrbot/bot_pipeline.py::experience_context` 根据可信 sourceScope 判断所属会话，研究任务没有发送 target 也可以回到来源群。不同群及其他私聊仍隔离。

合并同一动作时，本地回执负责填补 PostgreSQL 更新前的反馈空隙；持久目标的来源、任务类型、目标对象和目标描述保持权威。持久动作已有完成时间时，最终持久结果优先，不会被旧本地记录覆盖成较早状态。

`packages/database/execution-memory.ts` 在同源上下文的搜索经历中加入查询词，支持按查询词检索。原来的只读事实视图、权限和跨会话脱敏规则保留，不向其他会话输出私人查询词。结果观察时间优先使用实际证据时间，避免复用结果被误认成新取回的内容。

### 不同 Work 的相同搜索衔接

`packages/database/tool-effects.ts::recentRead` 从已有持久工具回执查找最近两分钟的成功视频搜索。严格匹配：

- tenant、bot、来源会话；自主动作通过已接受目标恢复来源。
- QQ generation、机器人配置修订、B站设置修订、账号 UID 与凭证修订。
- 查询词、分页、数量等实际参数。
- 原始结果必须 completed，不能取消，也不能是另一份复用结果。

`apps/node-agent/src/bilibili/executor.ts` 在现有身份及权限校验后使用结果，每个新 Work 仍有自己的 action ID 和完成回执。返回 `reused.actionId/observedAt`，保留原始证据时间；复用不会延长原结果有效期。

`packages/contracts/bilibili.ts` 为 search 增加可选 `fresh` 参数。内层明确需要最新结果时可设为 true，重新查询；仍遵守账号授权和风控冷却。已有调用无需修改。

服务冷却期间可以读取符合上述边界的现成结果，因为这不会请求 B站；未命中仍受冷却拦截。没有给评论、点赞、私信、动态等写操作增加复用，没有扩大任何工具权限。

没有另建模型、队列、常驻进程、记忆库或模糊语义任务合并。当前同一个 B站执行器的轻量任务本就串行，后续相同读取可在前一任务完成后命中持久结果；没有引入消费者互相取消的共享网络 Promise。

## 实际验证

| 验证 | 结果 |
|---|---|
| TypeScript 检查 | 最终版本 `tsc --noEmit` 通过 |
| 本地完整回归 | 自主协调器、生命周期、B站共 97/97，通过；约 201 秒 |
| 最终搜索用例 | 补充真实关联目标来源和 fresh 参数后，定向复验通过 |
| 本地执行经历回归 | 1/1，通过，覆盖会话、租户、账号和 API 重启读取 |
| Linux 最终定向回归 | 4/4：新目标立即派发、可信来源、防主人身份伪造、跨 Work 搜索复用、执行经历隔离 |
| Linux 原生 AstrBot 管线 | `tests/autonomous_pipeline_test.py` 完整运行通过；包含新增来源群研究、旧本地记录合并、持久结果优先断言 |
| 构建 | 对受影响服务器模块及维护入口定向生产编译通过 |
| 部署校验 | Windows 源码、Linux 源码和运行产物摘要一致 |

Linux 初次隔离测试因缺少测试 `.local` 目录及 SDK 路径不同而失败。补齐独立测试目录，将 SDK 引用指向真实 `/opt/astrbot` 后重跑通过；未削弱测试或修改生产 SDK。

## 部署与在线状态

16:13:45 验证：

- API 独立重启后 healthy；Agent PID 730881 保持。
- 本轮部署前有 3 个运行中的 QQ，3 个协议进程 PID 和创建时间全部保留，均在线；其他账号的 offline/stopped 状态保持不变。
- 等共享 AI 空闲后重载一次：PID 1225731 → 1239614，加载 Python 上下文修复；QQ 协议进程和登录 Session 没有重启。
- 自主协调器通过既有维护入口替换为修复版本，配置、目标和数据库保留。
- 实际工具清单已发布 `search.fresh`；Guard active。未改 48MiB、40 秒 PAGEOUT、Swap/zswap、离线清退策略。

部署目录和受影响文件备份：`/opt/fishbot-pg-cutover-20261031/control-final-c/collaboration-fix-20261104`。回退需先停后台自主调度，使用 `publish.py rollback` 恢复本轮文件，再恢复 API、B站执行器、自主协调器及空闲共享 AI 的旧运行代码；不能只恢复磁盘便认定内存中的代码也已回退。QQ 数据不在本轮部署清单内。

## Plan vs Actual 与边界

三项已证缺陷均有对应实现及 Linux 回归。范围扩展仅包括为搜索增加显式 fresh 参数、保留查询词与原观察时间，用于避免复用隐藏新鲜度及再次失去任务关联；均属于第三项修复的必要配套。

安全收窄：跨 Work 自动复用仅限参数一致的近期成功 B站视频搜索。没有按“看起来相似”合并一般任务，没有统一合并消息发送，也没有修改写操作幂等和 unknown 处理。不同查询、分页、数量或过期结果仍会重新执行。

尚未完成真实用户聊天中“新委托→自主研究→同主题追问→实际复用→最终回复”的整段再次验收。本轮没有伪造 QQ 入站或触发额外外部写操作；自然观察窗口尚无新的委托和复用样本。Linux 原生管线使用真实 AstrBot SDK、隔离模型/协议夹具，其通过不冒充真实 QQ 对话通过。

证据位于 `.local/collaboration-fix-20261104/`：`review.diff`、`tests.log`、`reuse-final.log`、`memory-tests.log`、`linux-final-tests.log`、`deploy.log`、`verification.log`、`manifest.json`。详细 Linux 运行证据保留在部署目录，账号数据不提交为公开报告。
