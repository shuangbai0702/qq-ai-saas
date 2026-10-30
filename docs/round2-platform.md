# 第二轮：平台固定内存与生产入口实测

日期2026-10-28，工作区 `D:\MYCODE\APP\fishbot\qq-ai-saas`。本轮没有替换数据库引擎；保留关系模型、SQL Migration、加密、CAS和原API权限边界。

## 一个必须纠正的旧测量口径

上一轮 `test-results/live-resource-samples.json` 中，API条目 `processes=7`，约662MiB实际是**API整棵进程树**，其中已含一个AstrBot和Mock协议；文件又单列相同AI/协议。它不是可靠的平台固定成本。原始文件完整保留，不能拿662直接减新数据来宣布优化收益。

新 `scripts/sample_resources.py` 建立唯一PID集合，把每个PID归到最具体的已登记组件（AI/协议子树优先于API/launcher祖先），输出分组件/唯一总计、各PID、RSS/USS/Linux PSS、CPU累计秒、宿主MemAvailable及可选cgroup memory.current/peak/events。JSON与CSV同轮输出。实际父/子进程fixture证明无PID重复、组件和等于总计；`rawTreeRssBytes`仅作诊断，**不能再求和**。采样器自身不计产品成本。

新同口径开发服务样本 `experiments/round2-platform/results/live-accounted.json`：Vite约127MiB、开发launcher约73MiB、旧配置API本体与其非bot子进程约348MiB；其余两套AI+Mock单独归属。这个时点不是容量压测，没有与其他轮次峰值相加。

## 已进入正式代码的优化

`packages/database/index.ts` 默认 `DB_MEMORY_PROFILE=compact`，使用PGlite公开`startParams`，继承其全部启动参数再覆盖：

```text
shared_buffers=8MB
work_mem=1MB
maintenance_work_mem=8MB
```

旧PGlite PostgreSQL默认128MB shared_buffers，对初期元数据负载过大。8MB允许同样的认证、事务、租户和配置写入；不是关闭持久化、记忆或消息功能。`DB_MEMORY_PROFILE=balanced`回退原默认，外部pg驱动分支不受该参数影响。无效配置明确拒绝，不静默回退。新增`tests/platform-memory.test.ts`查询真实pg_settings，验证实际生效、迁移、数据关开恢复和balanced回退；原API测试同时通过。

生产使用编译ESM，不常驻Vite或tsx：

```powershell
node scripts/production-build.mjs
node --max-old-space-size=128 scripts/production-start.mjs
```

前端仍由API单入口提供。编译器保留模块相对路径并复制所有SQL Migration；运行仍需要现有Python launcher/上游环境。`dist-server/build.json`记录源文件SHA256、构建时间与入口。启动器直接import，不另开一层Node进程，记录`<DATA_ROOT>/production-process.json`供采样。正式环境先设置`APP_ORIGIN`为实际入口；当前默认只绑定本机。

128MiB是Node老年代上限，**不是总进程内存上限**，WASM与native内存另计。实测它主要降低冷启动垃圾滞留/峰值，长一点的空闲样本中GC差异较小，不把短时几百MiB波动宣称永久优化。若以后控制面负载显著增加可提高该上限后复测。

## 可复现实验与结果

`scripts/benchmark-platform.py`依次创建独立新数据库/Node进程；API模式实际注册、创建20个机器人、列举20项，然后空闲采样。没有QQ/AI子进程，避免把运行面内存混入固定开销。0.25秒RSS/USS进程树采样；CPU为整个被测进程树累计时间。每个标签新建目录，拒绝覆盖旧数据。

```powershell
references/astrbot/.venv/Scripts/python.exe scripts/benchmark-platform.py --profiles-only --seconds 15 --label 新的唯一标签
```

同轮正式API结果 `results/formal-api-compact/summary.json`，Windows Node24.16.0 / PGlite0.5.12：

| 编译后模式 | 空闲RSS中位MiB | 空闲USS中位MiB | 冷启动峰RSS MiB | CPU累计秒 |
|---|---:|---:|---:|---:|
| API，balanced，old-space128 | 290.9 | 242.4 | 528.9 | 3.56 |
| API，compact，old-space128 | 158.9 | 112.3 | 485.5 | 3.42 |
| API+Agent，compact，old-space128 | 167.8 | 121.1 | 485.6 | 3.91 |
| API+Agent，compact，默认Node堆 | 165.6 | 118.9 | 565.9 | 3.73 |

明确收益：相同API路径下balanced→compact约少132MiB空闲RSS（约45%）；20个机器人元数据不需要20个DB实例。CPU没有显示显著增幅，样本太短不能作为吞吐结论。Supervisor较API-only增加约9MiB，此时20个机器人均停止，未计运行面。

原始分解实验 `results/shared-buffer-comparison`：Node-only约56MiB，导入API约72MiB；raw DB默认约257MiB、compact参数约127MiB。不能将这些不同进程的数值机械相加，其作用是定位导入/数据库开销。初轮`first-baseline`保留了默认Node的GC时机波动和失败记录，后续没有覆盖它。

**冷启动峰仍约486MiB**，主要包含新集群initdb/WASM初始化及V8垃圾滞留；稳态低于400不代表全部启动/峰值低于400。首次引导先完成数据库，再启动机器人有助于错开峰值，但仍需按整机峰值验收。PGlite`initialMemory`虽支持配置，现有WASM声明最低128MiB；尝试64MiB真实失败于LinkError，记录在`first-baseline/3-db-tuned/stderr.log`，没有把它用于正式代码。

## 实际功能验证

- `node scripts/production-check.mjs`：真实plain Node端口启动，无tsx/Vite；静态页、注册、创建机器人、Key加密不回传、优雅停机、进程重启后会话/数据库恢复通过。结果`production-check.json`；不启动QQ/AI，不冒充完整机器人测试。
- `npx tsx --test tests/platform-memory.test.ts tests/api.test.ts`：10项通过，0失败。
- `references/astrbot/.venv/Scripts/python.exe experiments/round2-platform/test_sampler.py`：父/子去重测试通过。
- `npx tsc --noEmit`通过。本轮最终全套测试由主任务另行统一执行。

## Linux实测与未执行项

未启用WSL/Docker系统功能，改用项目内便携QEMU（1536MiB、2vCPU、TCG），Alpine3.23.7/Linux6.18.58、Node24.22.1。CPU时长不代表生产。首次目录在tmpfs占RAM，真实OOM，失败日志保留；迁移到独立虚拟磁盘后四项均exitCode=0。

| Linux模式 | 空闲RSS MiB | 冷峰RSS MiB |
|---|---:|---:|
| API balanced old-space128 | 279.00 | 845.98 |
| API compact old-space128 | 154.50 | 842.33 |
| API+Agent compact old-space128 | 155.71 | 998.57 |
| API+Agent compact 默认堆 | 176.52 | 999.02 |

独立85秒统一采样末12点RSS/USS/PSS中位156.15/155.47/155.57MiB；该轮峰664.70MiB，不能覆盖另一轮近1GiB冷峰。完整导出在 `experiments/round2-platform/results/linux-guest/`，成功summary为 `imported/linux-tcg-disk-api/summary.json`，失败初轮也保留。VM源码归档对应其编译构建，后续健康轮询小优化未纳入该轮数值。虚拟机已正常关机，实验HTTP交换器已停。

这些是控制面测试，无Linux AstrBot/native QQ、无真实QQ在线/媒体/重连；cgroup未获得有效委派数据。Linux生产完整机器人和独立PostgreSQL比较仍未执行。没有证据证明换PostgreSQL更省内存。

## Plan vs Actual

完成固定成本去重、DB调参、无Vite/tsx编译单入口、持久化/停机恢复、Windows及Linux控制面原始数据。偏移：Linux完整机器人与cgroup未通过；独立PostgreSQL未比较。首次OOM保留，未把统计修正计为优化收益。最终全套23/23通过，完整范围和边界见 [交付报告](round2-delivery.md)。
