# 第二轮交付：共享实验、精简运行与真实 QR

日期：2026-10-28。实际工作区 `D:\MYCODE\APP\fishbot\qq-ai-saas`。未修改旧 Coopanion。第一轮文件及原始证据保存在 `experiments/round2-baseline-20261028-115125`，未删除已有测试；本目录没有 Git 元数据，没有执行提交/重置/清理。

## A–C：主要突破与共享隔离

**一个真实 AstrBot 可以管理多个 QQ 平台连接，并按平台路由不同配置档案、模型、Key、Persona 和会话。** 本轮实现了 1/2/4/8 逻辑机器人与每组 1/2/4 的可执行矩阵。QQ 和模型服务器为替身，AstrBot/OneBot/模型 SDK 为真实代码。源码与调用路径见 `experiments/round2-shared/README.md`、`worker.py` 和 `adapters/astrbot/tenant_tools.py`。

验证同群号、同发送者、不同 bot 的上下文/回复路由，热更新 A 不更改 B，删除 A 后 B 继续回复，图片事件穿过真实 SDK。还验证未授权管理请求、错误 self_id、跨 bot 工具发送和本地文件读取拒绝。发现 AstrBot 自动注入工具后增加了真实工具守卫，没有删除失败断言。

**这证明受控功能的逻辑隔离，不证明同进程任意 Python 插件的安全隔离。** 全局对象、数据库与进程内存仍共享；组崩溃影响组内所有成员。持久组调度、组恢复、租户日志、真实密钥分发及主动任务隔离尚未产品化。因此 shared 仅实验，正式平台继续 dedicated；没有给客户暴露不安全的共享模式。

## D：优化数据（MiB）

以下四 bot 使用同一采样器，PID 只计一次；同为 compact DB。总树包含真实 AI、四个模拟 QQ 进程、测试控制面/TS 加载器与模型替身，不是正式生产总内存。

| 四 bot 模式 | AI 核心合计 RSS | 测试完整树 RSS |
|---|---:|---:|
| 四份原版 AstrBot | 1080.6 | 1594.4 |
| 四份精简 AstrBot | 816.7 | 1290.2 |
| 两组，每组两个 | 412.6 | 928.8 |
| 一组，共享四个 | 205.3 | 719.7 |

原版独立→精简共享：AI 少 875.3 MiB（81%），测试整树少 874.7 MiB（55%）。不是声称四个真实在线 QQ 已达到 720 MiB。原始目录及更多矩阵见 `experiments/round2-shared/matrix-summary.json`。

正式落地两项独立收益：

- `slim_bootstrap.py` 延迟类型用途 SDK 和未选 Agent 导入，不改第三方快照、不替换实际模型 SDK。三轮导入基准 RSS 252.49→187.27 MiB；真实消息链 270.08→205.49 MiB。默认启用，`FISHBOT_ASTRBOT_SLIM=0` 可回退；结构校验拒绝上游代码漂移。任意插件的反射类型求值兼容性未保证，当前受控插件路径已回归。
- PGlite compact 将 shared_buffers 128→8MB，work_mem=1MB、maintenance_work_mem=8MB。Windows 同路径 API 空闲 290.9→158.9 MiB；API+Agent 167.8 MiB。关闭 Vite/tsx 的编译生产模式已运行。参数可用 `DB_MEMORY_PROFILE=balanced` 回退。没有盲换数据库。

**纠正历史统计：此前固定成本约 662 MiB 的样本把 API 子进程树及其中 bot 重复计算。** 修正计数不是优化收益。原始数据保留；本轮统一使用 unique PID 和独立进程对照。

## Linux 实测与峰值

独立便携 QEMU，Alpine 3.23.7 / Linux 6.18.58、Node 24.22.1、Python 3.12.16，1536MiB guest / 2 vCPU / TCG。没有启用系统 WSL、修改其他服务。TCG CPU 时间不能外推生产吞吐。实验已导出并关闭虚拟机。

磁盘目录新数据库测试：balanced API 空闲 279.0 MiB，compact API 154.50 MiB，compact API+Agent 155.71 MiB；后者冷启动峰 **998.57 MiB**。独立 PSS 采样末 12 点 RSS/USS/PSS 中位 156.15/155.47/155.57 MiB，峰 RSS 664.70 MiB（另一轮启动，不能替代最大冷峰）。无可用委派 cgroup 数据，没有宣称验证了 cgroup 硬配额。

首次运行目录在 tmpfs，占用 guest RAM，真实触发 OOM；迁移到独立虚拟磁盘后四个配置均 exitCode=0。失败日志及 dmesg 保留，没有把失败轮改写为通过。完整原始数据在 `experiments/round2-platform/results/linux-guest/`，成功结果 `imported/linux-tcg-disk-api`，PSS 数据 `imported/linux-control-pss-resources.*`。此 Linux 仅测试控制面，没有测试 Linux AstrBot/native QQ 完整机器人。

## E：真实 QQ 状态

NapCat v4.18.41 官方 Windows.Node 包（Node22 + QQ native）已自动安装并受控启动，真实二维码已通过平台鉴权 QR API 返回。`runner.mjs` 使用独立 profile/config/cache，管理 API 只绑定 loopback，按进程树停机；不改用户桌面 QQ。网页登录与状态 SSE 已接入此 Runner。平台只在 isLogin && coreReady && 身份确认后显示 online。

1/2/4 个真实 NapCat **登录前**完整树约 120.80/235.15/471.20 MiB，均生成真实 QR。不能把这些数当登录后或图片峰值。源码、安装校验、会话恢复入口和 raw 数据见 `docs/round2-qq.md`、`test-results/round2/napcat-prelogin-resources.json`。

用户已完成真实扫码。NapCat 报告 isLogin/coreReady 并确认身份，平台 state=online。随后通过平台启动命令重启协议进程，使用独立会话自动恢复在线，无需二次扫码；真实 AstrBot 就绪且 OneBot 连接建立。证据 `test-results/round2/real-qq-start-result.json`。尚未真实 QQ 对话/付费模型回复，尚未整个平台重启或主机重启验收。模型 Key 由网页保存，不发聊天。Euler 真执行到 fetch_qrcode→wtlogin.trans_emp→sign_provider，即生成 QR 已需要可用签名，现无可用 signer；原始 ConnectionError 已保留，并修复退出状态覆盖/死进程误报在线。

## F–G：容量与经济边界

**目前没有经过验收的完整真实在线 QQ 可售容量。** 2GB 挂两个可作为下一轮测试目标，不能宣称已通过；3–4 个更依赖真实协议峰值与共享产品化。4GB 的 4–8 个、8GB 的 8–16 个也只是测试候选范围。

真实扫码后追加15点同步采样：Windows 精简独立 AstrBot约200.80MiB + 已在线NapCat约185.30MiB = **386.10MiB RSS**，USS合计227.57MiB；不含共用控制面，未配置模型Key、未聊天。此前刚登录NapCat单独12点为196.59MiB，说明驻留量随阶段变化，不能只选最低点。原始 `complete-real-bot-online-samples.*`、`napcat-real-online-samples.*`，摘要 `complete-real-bot-summary.json`。共享四个AI摊销约51 + 已在线协议约185 ≈236MiB/客户只是跨实验估算，未实测四个真实QQ。均缺媒体/重连压力与Linux全链数据，不是容量承诺。模型上下文缓存、OS、文件缓存、数据库冷启动、并发启动必须额外预算。

容量模型：`N <= floor((总内存 - OS - 控制面 - 峰值余量) / 实测在线增量)`，同时满足冷启动/重连并发峰值和 CPU/IO。建议实际实验保留至少25%–35%负载余量，但该比例本身不是安全性证据。先启动数据库再分批 bot，避免峰值叠加；长时间实测后才批准销售数。

18.8元/月、60%基础设施毛利目标对应每客户成本≤7.52元。若可售 N=2/4/8/16，整台机器全部基础设施预算上限分别15.04/30.08/60.16/120.32元/月。实际毛利=`1-(服务器+磁盘+流量+其他基础设施)/(18.8*N)`；低利用率按实际付费数分摊。没有服务器报价，因此不编造毛利或采购建议。

## 验证

- `npm test`：23/23，0失败、0跳过，包含原有16项和真实多实例/共享隔离回归；日志 `test-results/round2/backend-tests-final.log`。
- `npm run build:production`：类型检查、编译 ESM、Vite 静态构建通过。
- `npm run check:production`：单端口静态页、真实注册/CRUD、Key 不回传、进程重启持久化通过。
- `WEB_URL=http://127.0.0.1:8787 npm run test:web`：真实浏览器注册、向导、显式 Mock QR/SSE、配置持久化、真实 AstrBot 就绪、手机排版、登出登录通过。QQ 和模型不冒充真实上线。
- 本地生产启动→Stop-Production→重启健康通过，原有数据库继续使用。管理浏览器另验证并发编辑冲突与管理员资源页，结果见 `test-results/round2/`。

## H–I：进入产品的部分与下一步

已进入正式代码：精简导入、compact DB、编译单入口、静态前端、停止 bot 减少无效探测、受控 NapCat 安装/Runner/QR、Euler 退出状态修复、去重采样。实验保留：shared Worker/工具守卫、共享容量矩阵、便携 Linux。

下一步按阻塞排序：①用户已扫码并验证协议重启恢复；继续填 Key，验收真实 QQ→真实模型→回复和整平台重启；②Linux 安装实际 QQ/AstrBot，测全树在线/群聊/媒体/重连峰值及24h以上运行；③共享组持久调度、密钥/日志边界和故障恢复后才接入 SaaS；④验证 cgroup 硬配额和异常插件行为；⑤依据完整峰值批准容量。签名路线独立继续，不能阻塞现有 NapCat 产品闭环。

## Plan vs Actual

| 原要求 | 实际结果 | 状态/偏移 |
|---|---|---|
| 完整资源基线且保留原始数据 | 独立/分组/共享同采样矩阵，历史快照保留；追加单个真实QQ在线同步采样 | 缺多个真实QQ、聊天媒体与长期负载 |
| 共享 AstrBot 配置与消息隔离 | 1/2/4/8矩阵、真实框架调用、热更新/工具守卫 | 原型完成；不将逻辑隔离等同进程安全隔离 |
| 共享安全后进入产品 | 未开放 shared，dedicated 保留 | 有意暂缓；插件/组恢复尚不满足 |
| AI 与平台实降内存 | SDK 延迟加载、DB 参数、无开发服务器 | 完成受控路径；插件反射兼容有限 |
| 真实 QQ 登录与回复恢复 | 扫码在线、协议进程重启自动恢复、AstrBot与OneBot连接通过 | 缺Key/真实对话、整平台重启与媒体验收 |
| Linux 完整资源测量 | Linux 控制面/PSS与真实 OOM 重测 | 部分；完整 QQ+AI、cgroup 未执行 |
| 长期与媒体压力/可售容量 | 短时空闲、连续消息、极小图片 fixture | 不足；未承诺可售容量 |
| 轻量自研 AI Worker 探针/独立 PostgreSQL比较 | 未执行 | 优先投入已有框架共享和真实 Runner，无结论 |
| 原有能力和测试不删 | 23项通过；生产 Web 真实回归 | 完成已列范围 |

新增默认值及边界：compact/slim 默认开启且可回退；共享未成为默认。未安装生产系统服务、未改旧项目或开放公网部署。
