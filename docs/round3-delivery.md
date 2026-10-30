# 第三轮交付报告 · 2026-10-28

实际工作区 `D:\MYCODE\APP\fishbot\qq-ai-saas`。继续原平台，没有新建另一套产品；旧 Coopanion、已有数据库、Master Key 和 QQ 登录会话保留。本报告替代 README 的旧状态说明，不覆盖第二轮历史数据。

## 1. 真实模型与 QQ 闭环

已真正使用 `https://nai.lunarbyte.pw/v1` 的 **`gpt-5.6-luna`**：`/models` 列出此 ID，聊天 200、Streaming 200 且结束标记正常、Tool Calling 200 且参数可解析。图片探测返回 502，错误模型返回 503；没有把图片能力标成可用。TLS 正常，usage 是上游报告值，未独立核账。早期短别名失败的证据保留，产品配置使用完整 ID。

证据 `test-results/round3/real-model-probe-1793168391407.json`。探测严格有限次数，Key 通过平台加密存储，不写源码、报告、前端返回或共享配置文件。

两个授权真实 QQ 已在线并属于同一个正式共享 AstrBot 组。第二 QQ 向 AI QQ 发私聊，实际 NapCat → OneBot → 原生 AstrBot Provider → 真实模型 → NapCat → 第二 QQ，已取得双方历史和 SDK 调用元数据。第二 QQ 不配置模型 Key，防止 AI 循环互聊。

真实样本包括自我介绍、多轮、显式事实记住绿茶、重启后召回、纠正为红茶、再次召回、不虚构搜索/截图、感谢与情绪表达。`real-scenarios.json` 保存六个有限场景，每项包含 QQ 消息 hash、真实模型、response ID、prompt hash、延迟。`real-persona-introduction.log` 保存新人格介绍。首次恢复失败没有隐去，记录在 `real-scenarios.log`；随后受控恢复，结果在 `real-scenarios-recovered.log`。

**主动聊天已真实通过**：收到一次邀请后，机器人先分两条气泡回复；没有新入站消息时主动聊起配白饭的小菜与红茶。`real-companion-history.log` 可核对时间顺序，`real-companion-telemetry.log` 中有主动尝试 1 次、真实 SDK 成功。测试的 60 秒模式已恢复正常：30 分钟沉默、1 小时间隔、每日上限 3 次、未回复上限 1 次、23–8 点安静，仅允许授权第二账号。不是让用户不断问一句才答一句。

真实群聊 @、图片/语音以及两个独立付费客户各自真实 Key 的并发尚未验收。群触发、同群号隔离和回复归属有真实 AstrBot + 协议/模型替身测试，不能代替真实群收发。

## 2. 大肥鱼行为还原

来源、固定提交和能力差异见 [人格与行为报告](round3-persona.md)。成功直接取得两条 B 站视频 metadata，确认 AstrBot 与 DSH 是不同实现线，不能混为同作者。

已落地：开源 v2.1 风格 Persona、可信 QQ 身份、短句、有限表情、真实沉默、私聊/群 @/白名单、冷却、连续消息合并、@ 后跟聊窗口、分条发送、原生历史、显式事实更新/遗忘、轻量关系与短时情绪、主动事件及防打扰限额。人格改动真正应用到所属 Bot。

提示词负责说话方式；程序负责身份、触发、时序、状态、记忆和实际发送。没有给每租户加常驻 Embedding 或另一个主动判断模型。

没有实现原 DSH 自动记忆衰减、七维情绪、B 站浏览、语音和提醒。`cortico-world-qq-final` 不能直接安装进 AstrBot，已复用其主动机制设计，未冒充原插件适配完成。

## 3. 正式共享运行组

Migration `002_runtime_groups.sql` 持久化 runtime_groups / group_members，Bot 包含 dedicated/shared、group ID；组有容量、节点、desired/observed、generation，成员有独立 revision 与状态。

`adapters/astrbot/shared.ts` + `shared-worker.py` 已接入正式 Agent，网页可选择共享模式。自动分配、容量溢出新组；动态添加不重启其他成员；单独更新 Persona/Key、暂停、删除；组崩溃、Agent 重启后按持久 desired state 恢复。旧 generation/revision 回执不能冒充新配置。默认容量 4，可设 1/2/4/8；dedicated 保留且仍是新建默认，不强迁用户。

共享 Provider/Persona/会话路由有 Bot 作用域；Key 仅进鉴权 IPC/内存；同群号、同 Sender 在不同 Bot 不共享会话。只加载平台受控能力。共享进程不提供任意 Python 插件安全隔离，一个进程崩溃影响整组。

正式测试覆盖两租户四逻辑 Bot 的模型/Key/Persona/历史/发送目标、热更新、独立启停/删除、容量、组崩溃及 Agent 恢复；额外 1/2/4/8 规模探针使用真实 AstrBot SDK + 替身协议与模型。两个真实 QQ 已共用一个组，但第二账号是无 Key 的接收方，不能宣传四个真实客户全部验收。

## 4. 审计和回归

详见 [关键路径审计](round3-audit.md)。修复包括迟到命令/配置回执、共享恢复重复重启、QR 过期取消串会话、停机后迟到发送、PID 重用、跨日志 chunk 密钥泄露、上游反射凭据、页面旧扫码状态冒充当前在线。

测试入口：`npm test`；构建 `npm run build:production`；产物检查 `npm run check:production`；浏览器 `npm run test:web`，连接已运行的 8787。旧 23 项保留并新增测试。最终运行结果以 `test-results/round3/full-tests-delivery.log` 为准；最终完整 **30 项通过，0 失败、0 跳过**（218.5秒），包含新增密钥反射测试。浏览器 `browser-delivery.log` 通过真实 API 注册登录、shared 创建、模拟扫码 SSE、配置/刷新持久化、实际 AstrBot 启动及手机布局；扫码部分明确为模拟，不混同真实 QQ 历史证据。

## 5. 资源证据与容量判断

以下全部区分实际 RSS/Working Set、采样峰值和推算，不把替身当真实 QQ。

### Windows 两个真实 QQ

`accounted-production-resources.json/csv`：30 样本、实际 53.5 秒，聊天后空闲；完整进程树分账，不把其他三套原有模拟机器人算进控制面。

| 组成 | 中位 RSS / Working Set |
|---|---:|
| API + PGlite + Agent 树 | 252.9 MiB |
| 一个共享 AstrBot | 205.8 MiB |
| 真实 NapCat QQ A | 194.8 MiB |
| 真实 NapCat QQ B | 196.3 MiB |
| 两 QQ 业务切片合计 | **849.0 MiB**，观测最高 849.9 MiB |

另外三套已有模拟机器人单独列账，所有现有运行合计 1678.4 MiB。先前 `real-two-qq-resource-summary.json` 把这些已有子树错误归给控制面；保留原始数据但不用于成本结论，纠正证据为 `accounted-resource-summary.json`。这些数据不是 OS 全机总用量，也不是图片/语音/重启峰值。

### 共享组规模：真实 AI 框架，QQ 与模型替身

`shared-sizes-1793169681382.json`，同轮测量：

| 组成员 | AI 空闲 | 文本后采样 | 并发批次时间（替身模型） |
|---:|---:|---:|---:|
| 1 | 180.0 MiB | 183.1 MiB | 444ms |
| 2 | 183.6 MiB | 206.5 MiB | 562ms |
| 4 | 204.6 MiB | 208.2 MiB | 979ms |
| 8 | 206.7 MiB | 212.2 MiB | 2128ms |

前后采样不是连续峰值。替身 QQ 约 78MiB 不纳入真实协议成本。四份第二轮精简 AI 历史基准 816.7MiB，相比本轮四共享 204.6MiB，AI 部分约少 612MiB；这是跨轮参考，不是同条件完整四真实 QQ 内存下降量，后者尚未测得。

### Linux 冷启动

在本地 QEMU Alpine 3.23.7 / kernel 6.18.58 / Node 24.22.1、1536MiB 来宾内存中，外部 50ms 采样运行 Migration001+002 / 100 次 CRUD：

| 模式 | 新库 peak RSS / 时间 | 已有库 peak RSS / 时间 |
|---|---|---|
| 默认 compact | 985.3MiB / 36.8s | 628.3MiB / 12.3s |
| lazy WASM 实验 | 956.3MiB / 40.4s | 667.8MiB / 12.0s |

两组均成功。lazy 对 warm 有退步，**没有采纳**。PGlite 默认 WASM 初始内存为 128MiB，新库分支还建立 initdb 实例并迁移文件系统；源码上是额外初始化开销的候选来源，尚未完成因果堆剖析。没有把退出后的 RSS=0 算空闲。原始 RSS/PSS/USS/MemAvailable 样本保存在 `experiments/round2-platform/results/linux-guest/round3-db-summary.json`。

Linux 尚未跑完整真实 NapCat + 共享 AstrBot 媒体/重连及长期负载。**目前不能承诺 2GB 可售 2、3 或 4 个完整客户**。稳态可尝试不代表冷启动可恢复、更不代表可售。4GB/8GB 也只有候选模型：`固定控制面 + ceil(N/组容量)×AI组 + N×QQ + OS + 媒体/重连/冷峰值余量`，须用 Linux 实测替换 Windows 数值。

18.8 元/月、60% 基础设施毛利目标意味着每客户基础设施预算 7.52 元/月；N 个客户服务器及其他基础设施总预算最多 `7.52N`。2/3/4/8 客户对应 15.04/22.56/30.08/60.16 元/月。未查询真实供应商报价、不含模型费，不据此声称实际利润。

截至最终状态核验，观察器已记录约 11 分钟；一次计划中的平台升级已自动恢复。当前一小时只读稳定性观察器仍单独保留，数据 `soak-*.ndjson`；只有出现完成 summary 才算该窗口完成。API 升级造成的预期离线也会如实记录。未做 24–72 小时验收。

## 6. 启动与复现

```powershell
cd D:\MYCODE\APP\fishbot\qq-ai-saas
npm run build:production
npm run check:production
npm run start:production
# 另一个终端停止；不要重复启动第二个数据库实例
.\scripts\Stop-Production.ps1
```

当前入口 <http://127.0.0.1:8787>，统一静态前端和 API，无常驻 Vite。真实 QQ 页 `/#/bots/b64c0d8f-d862-4b6c-a8db-56044ac991cf/qq`。Key 在网页配置页保存，不再发聊天。`.local/postgres`、`.local/master.key`、Bot QQ device/session 是恢复必需数据。

复现实验（有意使用独立实验数据库/测试账号，模型规模探针不连外部付费模型）：

```powershell
npx tsx experiments/round3-shared-benchmark.ts
references/astrbot/.venv/Scripts/python.exe scripts/sample_resources.py --help
node scripts/soak-watch.mjs 3600
```

Linux DB 探针：先构建服务器产物，在安装了 Node/Python/psutil 的 Linux 产品副本运行 `python3 experiments/round3-db/run.py`；它创建自己带时间戳的实验库，不使用生产 `.local/postgres`。正式 Linux 全量部署仍按 dedicated/shared 受控进程 + 私有 loopback 协议接口推进，未声称已经投产。

## 7. Plan vs Actual

| 原任务 | 实际结果 | 未收口 / 偏移原因 |
|---|---|---|
| P0 真实模型与 QQ | 完成 API chat/stream/tools、真实私聊/多轮/重启记忆和两 QQ 收发 | 图片 502；真实群未提供授权群测试；usage 未独立核账 |
| P1 大肥鱼体验 | 完成可选模板、身份、显式记忆、简关系、确定触发、连续/主动/分条；代表样本真实模型 | 未取得视频网盘人格包，不宣称完整原版；自动遗忘/七维情绪/语音/B站子 Agent 未实现 |
| P2 正式共享组 | 持久 migration、API/UI/Agent/Worker、配置独立、容量、故障恢复及四逻辑 Bot SDK 验证 | 两真实 QQ 仅一个调用模型；不是四真实付费客户验收；共享只适用于受控能力 |
| P3 代码审计 | 针对关键调用链修复具体缺陷并保留旧测试/失败记录 | 不是全仓无缺陷证明；PostgreSQL 分支、精确单成员资源隔离仍未完成 |
| P4 资源/冷启动 | Windows 完整分账、共享1/2/4/8、Linux DB冷暖对照和原始样本 | 冷峰值未解决；未完成 Linux 真实 QQ 媒体压力/24h，不能给可售容量 |

新增但合理的行为：主动尝试失败也占预算以防无限付费重试；共享模式维持 opt-in；正常配置更新尽量不重启 QQ；对既有历史保留而只清理过期 runtime metadata。这些不是删除原要求，边界均在测试和报告中公开。

## 下一轮直接任务

1. 用现有两个授权 QQ 做第二租户独立模型配置和真实并发隔离；有授权测试群后补 @、跟聊/沉默及回复引用。图片需先定位上游 502，不能默默降级宣称能看图。
2. 完成 Linux 整机两 QQ + 共享 AI 冷启动/媒体/恢复，先解决数据库峰值：比较预初始化与独立 PostgreSQL/SQLite 的同口径峰值、并发和可靠性，再决定迁移。
3. 增加有证据、可纠正的自动事实提取/遗忘和成熟插件受控适配；语音与 B 站作为独立可关闭能力，不把多个框架插件硬拼进共享进程。
4. 完成组级 OS 资源限制、历史/媒体配额、备份恢复演练及 24–72h soak，然后决定 2GB/4GB 可售数量。保留 dedicated 回退。

最终部署核验：API PID 2384，两个真实 QQ 自动恢复在线且共用 AI PID 85268，A revision7/applied7、B revision1/applied1。快照 `test-results/round3/final-live-status.log`。PID 为当时证据，后续重启会改变。
