# Pi 会话接手与实际执行记录（2026-10-31）

接手来源是 Pi 最后活跃会话 `3771a0eb-b2e0-48db-b066-a64a6f9d1c32`。实施依据为该会话最后的 `pasted-b21e9d73-6027-4f8a-8699-7b717874cb84-pasted-text-7aff107f.txt`，继承内存优先、保护数据库/会话/密钥、真实验收、必要生产操作已授权和不执行 Git 的要求。历史压缩摘要曾失败，因此依据原始用户指令、现有源码和服务器状态重新确认，未照旧摘要重复切库。

接手前尚未读到禁止 Git 的历史要求时，执行过只读状态查询；读到后未再执行任何 Git 操作。工作区已有大量修改，本文仅记录本次新增工作，不把先前部署和功能变更算作本次产出。

## 已核实的生产基线

PostgreSQL 和 API/Agent 拆分已由上一轮完成，详见 `postgres-production-cutover-20261031.md`。本次核实运行库仍为 `fishbot_cutover_20261031c`，API 返回 `database=postgresql, agent=false`，真实 OneBot 返回 `online=true, good=true`。API/Agent/PG 容器 StartedAt 与切换完成时一致，本次没有重启这些容器，没有启动旧 PGlite owner。

接手时没有 PostgreSQL 定时备份；内存 controller/guard 已指向新生产容器。controller 是运行但未开机启用，guard 已开机启用。没有将两者误判为旧切换文档中的 inactive 状态，也没有覆盖远端 guard 与本地版本之间的既有差异。

## 本次实现和验证

### PostgreSQL 自动备份

新增 `scripts/postgres-backup.py`，部署为 `/usr/local/sbin/fishbot-postgres-backup.py`。`fishbot-pg-backup.timer` 已启用，每日主机时间 04:15 加 0–15 分钟随机延迟执行，保留最近 7 个完整备份对。备份目录 `/var/backups/fishbot/postgresql`，目录 0700、文件 0600。

采用 PostgreSQL 自身一致性 `pg_dump --format=custom`，不停止生产进程。先写 partial，验证 archive 可读，再恢复到 UUID 命名的全新验证数据库；`pg_restore --exit-on-error` 完成并检查表数后才发布 dump/manifest 和清理旧备份。恢复验证始终只删除本次创建的验证库。使用 flock 防止定时/手动任务重叠，失败不发布新备份、不清理旧备份。

首次真实备份与恢复成功：

- 文件：`fishbot-pg-20261031T071258175382Z.dump`，69,002 字节。
- SHA-256：`3b4b0db353cb0b624967057a9b132992641338dd3444cd3f211c016d261795c8`，复核匹配。
- 独立恢复库包含 17 张 public 表，验证库已正常删除，systemd 退出码 0，timer active/enabled。

该备份仅覆盖 PostgreSQL，不含 master.key、QQ 原生会话、AI SQLite 或媒体。旧完整备份保留，但不能代替新写入后的运行数据备份；本机备份也没有异机容灾能力。本次未虚称已完成整套运行卷的持续备份。

### 按所属 AI 分组唤醒和回收

修改 `scripts/linux-memory-controller.py` 与 `scripts/linux-pageout.py`：原先任何 AI 的活动会唤醒全部 AI，而且所有回收助手共用一个活动标记。现按经过 PID/进程出生时间验证的真实 Owner 计算 AI 活动；共享 AI 活动继续唤醒共用的 QQ/Agent/API/PG，但不唤醒无关独立 AI。回收助手改读所属 cgroup 的活动标记，控制器也只因同一 Owner 活动而中断该助手。

远端旧文件和 service 保存在 `/opt/fishbot-pg-cutover-20261031/codex-validation/memory-before-1793430958/`。没有替换 guard。独立 AI 空闲软阈值新增 `FISHBOT_MEMORY_DEDICATED_AI_MIB=16`，其他既有阈值仍为 128 MiB，活动期间放宽为 max，memory.max 未修改。

同一独立 AI PID `3157136` 的实际 `/proc/smaps_rollup` 连续采样中：USS 从 144,064,512 降到 4,198,400 字节（约 137.4 → 4.0 MiB），PSS 从 146,669,568 降到 6,803,456 字节。pageout 助手退出 0，QQ 保持 online，相关容器 OOM/OOM-kill 均为 0。这是一次冷页回收结果，不代表长期固定物理占用或所有服务都能保持 16 MiB。

进程迁入子 cgroup 前的内存 charge 会留在父级，所以 child memory.current 很低不能证明节省；本次使用进程 USS/PSS 验证。PostgreSQL 各进程共享映射的 Swap 也不能直接相加当作独占交换量。

### 回退保护和真实 QQ

独立 guard 的 controlled-trigger 演练通过：注册的全部 memory.high 恢复 max，真实 QQ 仍在线，API/Agent/PG 容器身份和 StartedAt 不变。确认健康后保留本次测试 marker，恢复 controller。没有自动清除未知故障 marker，也没有重启 QQ。证据为 `/var/lib/fishbot-memory-control/codex-guard-acceptance.json`。

只向既有授权 owner `768687099` 发送一条验收文字，OneBot retcode 0，message ID `1512955851`，发送耗时约 151.7 ms；get_msg 查询成功。该耗时不是 AI 回复延迟或接收确认。采样中共享 AI ready、成员事件计数增长，但模型请求/回复指标为空，不能据此声称完成生产推理闭环。

## 回归和差异复核

- TypeScript `tsc --noEmit` 退出 0。
- controller/guard Python 定向测试 14/14 通过；新增 Owner 隔离测试。
- 备份测试在真实 Linux 上 3/3 通过，覆盖保留范围、恢复失败只删除新验证库、dump 失败不发布/不清理旧备份。Windows 同组为 2 通过、1 个 Linux flock 测试跳过。
- 完整 TypeScript 回归执行完毕，116 项中 115 通过、1 失败，耗时约 499.6 秒。失败的是社交图片集成测试：泛称“请发图片”仍经过随机表情概率，却断言必发聊天表情包。
- 依据现有 `requests_meme`/`allow_meme` 的明确合同，将该测试输入改为“请发表情包图片”，将 memeProbability 设为 0，保留真实 Image、冷却和租户隔离断言；明确验证显式表情请求绕过概率而不绕过冷却。生产发送逻辑未改。该文件 2/2 复验通过。未再重跑整套 116 项，不能报告“修改后全量 116/116”。

完整回归日志：`.local/codex-handoff-regression-20261031.log`；复验日志：`.local/codex-handoff-social-recheck-20261031.log`。部署源码 SHA 与服务证据记录在 `.local/codex-handoff-final-evidence-20261031.json`，远端同份为 `/opt/fishbot-pg-cutover-20261031/codex-validation/final-evidence.json`。

## Plan vs Actual

| 原要求 | 当前实际和证据 | 状态 |
| --- | --- | --- |
| 完成 PG 生产切换、保留数据和 key | 上轮已切换；本轮确认仍使用新 PG，新增真实备份/恢复验证，没有重复迁移 | 切换已完成，完整运行数据持续备份尚缺 |
| 完成 API/Agent 解耦 | 上轮已有独立重启连续采样证据；本轮生产身份保持，没有重复扰动 QQ | 已核实，整机/gate 重启未演练 |
| 激进真实物理内存优化 | 修正跨 Owner 误唤醒，独立 AI 16 MiB 软阈值和实际 USS 下降已验证；其他组仍按活动放宽 | 部分完成 |
| 异常回退保护 | 本轮 controlled-trigger 实际放宽且 QQ 在线，无运行容器重启 | 受控触发完成，未重新诱导生产 host PSI 故障 |
| 真实收发、AI 回复、额外 P95 ≤5 秒 | 本轮真实文字发送和查询完成；没有受控入站到 AI 回复的配对样本 | 未完成生产推理/P95 验收 |
| 最大稳定密度，目标十个低活跃 QQ | 当前仅有一只真实在线 QQ，另有共享 AI、独立 AI 和 mock，不能按 cgroup 或 ready 数量折算十号容量 | 未验收 |
| 正式持久内存策略 | controller active 但 disabled，guard active/enabled；回退保持锁定，需核实健康后恢复 | 开机和容器重建后的自动恢复尚未完成 |

合理偏移：没有照旧摘要重做已提交迁移；新增定时备份以保护切换后的 PG 写入；完整回归发现随机断言后修正测试输入。没有扩展 API/Agent 权限或共享租户边界，没有改变客户配置、密钥、QQ hostname、消息概率或生产发送行为。

尚缺真实生产入站/AI 回复的对照样本、十个真实账号负载、整机/gate 重启和持久内存控制器恢复验证。因此本次不能宣称全部 FishBot V1 验收完成，也不能用一次低 USS 宣称十号稳定容量。
