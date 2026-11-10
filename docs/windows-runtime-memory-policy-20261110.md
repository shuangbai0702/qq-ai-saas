# Windows 运行内存管理接入（2026-11-10）

以本次只读调查的华为 Linux 生效配置为基准：通用 helper 为 `FISHBOT_MEMORY_POLICY=off`，独立宿主 controller/guard 生效；空闲 18 秒、回收间隔 40 秒，NapCat/共享 AI/独立 AI 为 48/72/24 MiB，基础 API/Agent/PostgreSQL 为 128 MiB。API 和 Agent 实际均用 Node `--max-old-space-size=128`。`agent-memory.ts` 中 192 MiB 函数当前无启动调用，不能据此调整部署预算。

本文件是源码接入合同与验证记录，不证明移动云已部署或完成真实 OOM 验收。

## 启动与所有权

由部署 supervisor 维护三个独立基础服务，给 API/Agent 明确传入 `--max-old-space-size=128`。关闭通用 helper，避免叠加两套回收。以 supervisor 托管 guard（退出后 1 秒重启），随后托管 controller（退出后 10 秒重启）；两个 Python 均需 `psutil`。

```text
python -B scripts/windows-memory-guard.py --state ABS_STATE_DIR --health-url http://127.0.0.1:8787/v1/health
python -B scripts/windows-memory-controller.py --root ABS_DATA_ROOT --state ABS_STATE_DIR
```

`--root` 是运行数据根，包含 `bots/<id>/{ai,protocol}.process.json`。共享组是 `bots/group-*/ai.process.json`，作为一个实例计预算；不按共享组成员重复计数。兼容数据根下的 `group-*` 目录。

基础服务记录由 supervisor 原子写入同一状态目录：

```text
ABS_STATE_DIR/services/control-api.process.json
ABS_STATE_DIR/services/agent.process.json
ABS_STATE_DIR/services/postgres.process.json
```

每文件 JSON：`{"pid":1234,"created":1794280000.123,"root":"D:/FishBot/source"}`。`created` 必须是 `psutil.Process(pid).create_time()` 的 Unix 秒，不能用启动命令时间替代。`root` 必须是该进程 argv 中可验证的绝对归属目录：API/Agent 采用源码根与绝对入口路径；PostgreSQL 采用 `-D` 的绝对数据目录。仅 PID、进程名或文件存在均不证明所有权。

创建 `services` 目录表示启用三基础服务合同：guard 要求三份记录完整、当前进程出生时间和 argv 所有权一致。supervisor 可先创建目录，随后逐个启动并发布记录；guard 在启动未健康时暂停回收，三记录与 API 连续健康后按冷却恢复，不需要手工删除暂停标记。更换进程必须在旧句柄退出后发布新 PID/出生时间，停止应验证句柄/出生时间，不能仅凭旧 PID 操作。controller 不承担进程启动、终止或 OOM 重启。

## 配置与状态

| 环境变量 | 默认值 / 职责 |
| --- | --- |
| `FISHBOT_MEMORY_POLICY` | API/Agent 设 `off`，关闭旧 helper 的原生回收 |
| `FISHBOT_MEMORY_IDLE_SECONDS` | `18`，空闲时间 |
| `FISHBOT_MEMORY_RECLAIM_SECONDS` | `40`，有效回收后的再次尝试间隔 |
| `FISHBOT_MEMORY_IDLE_MIB` | `128`，Mock 回收目标默认值 |
| `FISHBOT_MEMORY_NAPCAT_MIB` | `48` |
| `FISHBOT_MEMORY_SHARED_AI_MIB` | `72` |
| `FISHBOT_MEMORY_DEDICATED_AI_MIB` | `24` |
| `FISHBOT_MEMORY_CONTROL_API_MIB` | `128` |
| `FISHBOT_MEMORY_AGENT_MIB` | `128` |
| `FISHBOT_MEMORY_POSTGRES_MIB` | `128` |
| `FISHBOT_MEMORY_MOCK_MIB` | 覆盖 Mock 默认目标 |

进程树 CPU 增量超过 0.2 秒视为活动。AI 还读取其归属目录的 `memory-activity.json` 与 `ai/memory-activity.json`；校验 PID、出生时间、有限时间戳、不能早于出生或超过当前时间 5 秒；`active=true` 仅在 120 秒内有效。协议和基础服务不借用其他 AI 的活动文件。活动期不主动回收。

controller 在空闲且进程树 RSS 超过角色目标时调用 Windows `EmptyWorkingSet`，再次核验磁盘记录和进程寿命。低于 1 MiB 的 RSS 减少或原生失败视为无效尝试，重试间隔 80、160、320、600 秒；有效减少恢复为 40 秒。`reclaim-state.json` 以数据根、开机时间、PID/出生时间隔离退避，重启不绕过；暂停恢复重新观察空闲周期。

状态目录由两个服务独占写入，不能放入可供普通租户修改的目录。`controller.lock`/`guard.lock` 为 Windows 进程级文件锁；`guard-heartbeat`、`armed` 分别证明最近守卫/控制器循环。`controller-state.json` 含每实例角色、目标、RSS、决策、原生结果，明确 `quota=working-set-target-only`。指标是 RSS 工作集，不是 Linux PSS 或 `memory.current`。

guard 每 2 秒检查 API。连续两次失败、可用内存低于 384 MiB、受管基础服务寿命变化/记录缺失、controller 心跳超过 15 秒会写 `disabled` 暂停。controller 要求 guard 心跳不超过 5 秒，否则失败退出；暂停期间不继续原生回收。`recovery.json` 共用 Linux 的冷却计数，连续健康 30 秒后恢复，重复故障冷却增至 600 秒；仅连续运行健康 600 秒重置故障计数。controller 暂停期间旧 `armed` 不阻止恢复。Windows 已被 trim 的工作集可立即增长，无需像 Linux 那样写回 `memory.high=max`。

## 必要平台差异与恢复边界

Windows 目标是软工作集回收门槛，不能宣称实现 Linux cgroup `memory.high` 的持续节流，或实现提交内存硬上限。Windows 没有 Linux PSI、`memory.events`、zswap 或 cgroup 页记账；guard 用可用内存、健康与受管进程寿命保护，不能把寿命变化直接归因为 OOM。宿主换页文件由部署明确配置，脚本不更改系统换页设置。

基础服务 OOM/异常退出必须由 supervisor 对真实退出句柄重启，保留数据库与运行数据；PostgreSQL 使用原数据目录恢复。AI/协议消失后沿用 NodeAgent 的当前命令账本与期望状态恢复：只修复缺失组件，保留健康 QQ；启动未就绪上限 120 秒，三次快速尝试后 10 分钟退避，每小时最多 9 次。该链处理进程消失，不声称识别所有 OOM 原因。

重启后的发送账本保留 `unknown`/`partial`，`dispatching` 转为 `unknown`，不把超时、进程退出或未明 ACK 变成重发许可。本次修复 `BotPipeline` 原先用 `os.kill(pid,0)` 探活的问题：Windows 对该调用使用终止进程语义，现改为 `psutil` 只读状态探测；保留活 owner，释放已退出 owner。[Python 官方说明](https://docs.python.org/3.12/library/os.html#os.kill)

## 实际验证与 Plan vs Actual

Windows 策略测试 8 项通过：含角色预算、原生失败退避、进程记录换代、守卫冷却、基础服务记录合同、退避持久化；实际 64 MiB 独立子进程回收后内存内容仍正确；实际独立 guard/controller 启动、回收、guard 退出后 controller 停止而 worker 存活。通用 helper/行为集成 24 项、快照 10 项、Linux controller 20 项与 guard 7 项通过。Node Agent/监控/恢复针对性测试 12 项通过；pipeline 11 项通过，含实际活进程不被探活终止与退出后释放、partial/unknown 重启不重发。`send_lifecycle_test.py` 是顶层异步脚本，发现测试时导入执行了其顶层场景并输出三条 PASS，不能把 unittest 的 `Ran 0` 当用例数。

计划中跨平台预算、统计、回收、异常恢复检查已对应到以上实现和现有链；基础服务 supervisor 启动/重启与上线仍需部署方接入。合理偏移是 Windows 用原生工作集回收与守卫适配，保留 Linux cgroup/PSI 策略，未移植内核专属接口。未运行：真实服务 OOM、受控全机压力、PostgreSQL 崩溃恢复、真实 QQ 消息 P95 与持续生产容量；当前证据不能宣称这些验收完成。

Linux 最终验证需目标机可用的同版源码、`psutil`、现有有效 controller/guard、Docker/cgroup v2 读权限。做只读配置/PID出生时间、PSS/USS、cgroup与OOM计数前后核对；受控 pageout/guard触发、暂停与重启验收须在隔离 cgroup/实例运行，生产 QQ 保持原 PID/出生时间，不制造宿主 OOM。涉及真实模型或 QQ 回执的验收另需可测试身份与明确测试目标。
