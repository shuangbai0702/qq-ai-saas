# FishBot 极致代码整洁与架构简化实施报告

日期：2026-11-12。状态：代码已提交，隔离验证完成；未部署、未修改生产数据库。

## 1. 基线、协作和提交

- 调查起点：`48ddb59319433f60d8a6734d903ce5ac56629dad`，分支 `main`。上轮整改 `8c29f54` 不计入本轮成果。
- 安全引用：`safety/extreme-cleanliness-20261112-48ddb59`；修改前差异和状态保存在忽略目录 `.local/extreme-cleanliness-20261112/`。
- 调查期间，另一维护任务提交 `1f97679`、`f00ab23`、`57b6713`，完成原有提示词及检查点修改。本轮没有接管其文件、回滚其提交或把其成果计为本轮产出。
- 集成验证基底：`57b671304da854e050a6849c4e07fc7eb7270d77`。隔离工作树只叠加本轮明确拥有的 56 个路径，提交前逐文件比对内容及删除状态。
- 本轮主体代码 SHA：`7deca171b031a6b5b30b7c36e690c134f6117d4c`，`refactor: retire unused prototypes and unify runtime ownership`；最终代码 SHA：`99719f1742df83aefcef1fd7d0e407d2caaff755`，补清未用 random 导入和失效说明。报告后续独立提交；报告提交 SHA 用 `git log -1 --format=%H -- docs/FishBot-极致代码整洁与架构简化实施报告.md` 定位，避免正文自引用哈希。
- 提交采用精确路径清单，未加入私有数据、密钥、测试缓存、旧的未跟踪研究资料。已有架构收敛报告的其他维护修改保持未提交。

## 2. 分析范围与方法

覆盖 `adapters/`、`apps/`、`packages/`、`scripts/`、`docker/`、`templates/` 和根配置的生产入口、构建、注册和部署关系，重点追踪：控制 API → NodeAgent/ProcessHost → dedicated/shared AstrBot → Python behavior/provider/tool hooks → Journal/记忆/任务及平台回执。

Python AST 对核心平铺的 80 个 AstrBot Python 模块建立导入和定义清单；TypeScript compiler AST 对分析时的 136 个 TS/TSX/JS/MJS 源文件记录 273 条仓内导入/导出边，区分 type-only、字面量动态导入与计算导入。该图未发现静态运行时强连通环，但不能证明计算导入、SDK hook 和 Monkey Patch 没有运行时依赖。结合真实安装器、CLI、构建清单和数据库名称继续判断，未以“零静态引用”直接批量删除。

部署资产调查覆盖脚本、Docker 和模板清单，区分实际运行资产与历史实验。检查 PostgreSQL repositories/migrations 消费者、四个生产 SQLite 的只读计数及关联、容器 build manifest、当前 control release 的维护调用方。没有遍历依赖缓存、全盘或第三方源码来制造扫描数量。

未覆盖：全仓每个异常排列、付费模型行为 A/B、真实 QQ/B站动作回放、生产 PostgreSQL 全量历史行审计、实时共享组完整成员拓扑、所有源码外控制脚本的可重现构建。静态图与有限采样不等于证明全仓再无废代码。

## 3. 已删除：原型链和无职责接口

| 删除项 | 原使用方与确认依据 | 当前承担能力的位置 |
|---|---|---|
| `memory_search.py`、`semantic_context.py` | BehaviorStore 曾构造 `search`，生产 provider/ContextBuilder 不读取；检索调用只来自旧 semantic_context 和其测试 | `memory_scope.fact_rows`、`ScopedMemory.recall`、ContextBuilder foundation |
| `agent-memory.ts` 和对应测试 | 只有原型测试导入；无运行入口、配置、CLI 消费 `prepareAgentHeap` 等接口 | 当前 Agent/生产启动的实际资源配置保持 |
| `split_context_snapshot` | 旧分离快照原型仅测试调用；真实 provider 使用 `request_context_view` | 现行 transport/Journal 请求视图 |
| `hosted_memory.self_report` | 旧正则自述助手无生产调用 | 现行模型认知产品与 companion 写入链 |
| `BehaviorStore.reserve_interjection`、`eligible` 未读 rng 参数 | reserve 只有测试旧入口；真实群发送走 autonomy 授权与预算 | 当前 `autonomy.reserve_send` 与真实准入 |
| `finish_memory_view` | 纯 `return shared`，没有读取、校验或副作用 | 删除函数与全部当前调用方，直接使用既有 context |
| `ReadonlyJournal.discard`、retain 的 stage 参数 | 前者只转发 abort，后者未读取 stage；同步更新实际调用链 | `abort_candidate`、既有阶段判定 |
| `AiAdapter`、restart/getHealth/validateConfig 空职责层 | AiAdapter 无消费者且不描述实际 probe；restart 无调用；其余包装并入当前 provision/probe | AstrBotAdapter 的真实入口与验证仍在 |
| `ProtocolAdapter.exportSessionRef` 及三实现、Shared 构造空参数 | 无持久化名称、动态注册或当前调用者；同步修改所有当前构造点 | 协议登录、身份、健康与 stop 保持 |
| coordinator 可选 isolation、`consecutiveFaults`、finish 未读 sleep 参数 | isolation 无实际生产配置接入；SQL 专属该分支；sleep 本来不被 finish 使用 | 真实错误分类、账本、30 秒退避和 wake/activity 调度保持 |

正式退役五个旧双 QQ 手动实验脚本：`round3-live.mjs`、`round3-real-scenarios.mjs`、`social-live.mjs`、`memory-synthesis-live.mjs`、`qq-pair-lab.mjs`。它们形成旧实验内部调用岛，依赖旧的私有双账号配置、固定早期模型/实验流程；不属于 package 生产启动或现行发布入口。历史 social-integration 文档继续保留，并明确旧命令已退役。用户资料和历史结果未删除。

线上旧镜像仍包含这些脚本副本；当前 control release 的相关代码扫描没有找到其调用，不能写成“线上已删除”。有意不兼容的是这些过时手动实验和废弃内部 API，当前 QQ/B站产品工具没有缩减。

旧原型专用测试随接口退役；事实更正、遗忘、Scope 隔离、当前 foundation 与持久化合同改测真实入口。旧 Hold 三项测试改为缺失原文显式标记、合法检查点可用、未确认回复不伪装已确认、下一批 owned 输入不误删。没有恢复 Hold 来满足旧测试。

## 4. 已重构：唯一 Owner 和直接数据流

1. **收藏索引归属收敛。** HTTP 和 Agent 统一使用既有 `qq-favorite-index` 的目录、读取、跨进程锁和写入。删除 API 额外 Promise 锁 Map 和重复索引读取/写入路径。Windows rename 的短重试移入共同 writer；同步进度 `sync.json` 仍由同步任务管理，它不属于收藏索引事实。并发相同版本八次编辑，只有一次成功，其余版本冲突，原有语义审计字段保留。
2. **在线事实收敛。** 管理 API 查询复用 `runtimeOnlineSql`，移除独立字符串时间和 JSON 真值解释；工具预算/回执调用方不再重复解释 desired/deleted。JS helper 明确只有 `desired_state === running` 可在线，未知/缺失状态拒绝。既有时间有效性、90 秒期限与 5 秒未来偏移、generation/revision、租户及 epoch 边界保留。
3. **Journal 持久化收敛。** commit 与 retain_submitted 共用 `_commit_candidate`。入口自己的合法性检查仍独立；共同持久化失败时，通过 submitted 标志保留原结算区别：确定未提交可释放许可，提交结果未知不能退款。没有引入第三套提交状态。
4. **维护 tick 单一入口。** 删除 Favorite/Bilibili installer 对 Agent.tick 的重复包装、mutable hooks 和对应回滚分支。canonical tick 本来就读取当前维护对象；安装器只更换其拥有的对象，保留 tick/shutdown 身份、子进程、PID 和日志管线。维护边界使用真实 `controlIdle`，删除旧 busy 回退。
5. **前端传输统一。** QQ 收藏读取与写入使用既有 `api`，删除自行 fetch/JSON/错误解释。保留页面 owner、序列、取消、轮询清理；写入未知提示和超时合同统一，不增加写入重试。写后刷新失败与写入失败分别捕获，刷新不得覆盖原操作错误。

业务 PostgreSQL 任务/执行账本、SQLite Work/入站、权威 Journal、媒体归属和模型认知产品仍为独立职责。未因名字近似合并它们。

## 5. 已实现迁移与生产未迁移的区别

四库真实 `memory_search` 行数为 6/5/0/7，均为 fact 派生项，均有两项 fact trigger。Facts 总行 213，active 202，采样未发现重复 active key；索引内容并不等于完整事实源。

BehaviorStore 启动迁移删除旧 FTS 虚表和四个可能存在的旧 trigger；当前生产两 trigger 包含其中。使用 SAVEPOINT 包住 DDL，失败完整回滚并向上抛出；临时 secure_delete 配置在成功/异常后恢复。测试确认旧库首次启动、重复启动、更正与遗忘、不同主体隔离、foundation、Journal 原始字节和失败回滚。

**完成的是代码侧迁移及隔离旧库验证；生产四库仍未执行迁移。** 未直接下生产 DDL、未删除事实/摘要/Journal，未宣称 WAL、备份或外部副本已物理擦除。没有新增永久双写或第二原文池。

## 6. 仍必要：有实际存量或外部调用证据

| 保留内容 | 实际证据与移除条件 |
|---|---|
| Journal v1/v2、archive/cutover 读取 | 当前 Journal v1=1、v2=3，全部 ready；operator archive 18，其中 9 available，10 条缺 journal.version；cutover 19，其中 13 available。必须先证明有效前缀可继续读取，不能原地改历史或偷触发压缩 |
| Coverage/Hold/pending/issue 存量读取 | coverage 979 均关联现存摘要；Hold126，其中13关联现存轮；pending_rounds66均有关联；unresolved170；issue665。不能将整表当无用垃圾；没有恢复其旧永久 Hold 准入机制 |
| maintenance 模块与安装 API | 线上 `r4-foreground-maintenance.js` 调用三安装器；当前 control release `agent-ops.mjs` 调用 Bilibili installer。先回收源码外消费者并建立可重现发布，才可能退役整个模块 |
| native-face-policy、协议 capabilities、NapCat runtime patch | 分别有当前 rollout CLI、Bot API 和启动/构建真实消费者；不是零引用原型 |
| shared_context、SDK hooks、反射工具注册、冷启动恢复 | 四库 shared_context 均 active，behavior/bot_pipeline 等当前调用，不能按静态未导入直接删除 |

## 7. 复杂度实际变化

只统计本轮拥有的生产路径，不混入并行提示词修改：文件 31 → 23，Python 模块 8 → 6，Python AST 函数/类定义 231 → 223，文本行 7305 → 7012。另删除一份纯原型测试。未增加第三方依赖，未增加新调度器、状态机、兼容包装或通用错误框架。

更有意义的变化是：废弃检索链 2 模块 → 0；收藏索引 HTTP/Agent 两套读写解释 → 一个共同实现，锁从额外 API Map + 文件锁 → 共同文件锁；Journal 两段持久化主体 → 一段；Favorite/Bilibili 两个 tick 包装 → 0；管理在线 SQL 独立解释 → 既有共同 SQL；收藏 fetch 旁路 → 既有 api。迁移增加的事务代码承担真实旧库退出责任，不能单纯用行数抵扣价值。

## 8. 实际验证与已知失败

隔离 candidate 基于 `57b6713` 加本轮文件；源码使用独立 Git 工作树，只复用已安装依赖/SDK。没有真实 QQ/B站发送、付费模型调用或 unknown 回放。

- `npm run build:production`：通过，含 TypeScript 检查、服务端构建、独立 maintenance bundle 和 Vite。保留现有前端 chunk >500KB 提示，未更改门槛。
- Node `tsx --test --test-concurrency=1`：admin-operations、autonomous-lifecycle/coordinator/activity、onboarding/frontend-status、shared-health/recovery、agent-operation-isolation、autonomous/foreground-maintenance、qq-favorites、code-review-favorites、qq-favorite-index，**91/91 通过**。
- Bilibili 四项定向测试：completed tasks、continuation grants、QQ/Bili budget、QQ unknown，**4/4 通过**。没有将未运行的其余 Bilibili 测试记为通过。
- Python 21 个文件共 **288 项：280 通过，7 失败、1 错误**。15 个原先完整通过文件加最后三文件均全过；另外三文件含下述基线失败。完整通过文件包括 memory_candidate_retirement、readonly_journal、context_reading_journal、context_transport、final_remediation_20261107、semantic_runtime、bot_pipeline、config_audit、companion_state、d2_audience、hosted_memory、p1_semantic_recall、shared_memory、summary_material、semantic_batch_summary、semantic_discard、journal_storage、semantic_journal_retirement。覆盖 34/35 阈值、管理员授权、缺原文、A/B/旧库恢复、不可变前缀、任务/约定及跨 Scope 退休边界。
- `node --import tsx tests/qq-favorites-browser.ts`：生产前端构建配隔离测试 API，启停、编辑、刷新持久化、390px 布局通过。首次被真实公告弹窗阻挡；测试补正常点击“知道了”后通过，原业务断言未删除。
- `git diff --check` 通过。主体提交前暂存清单与本轮 56 路径精确一致，逐文件与隔离候选比对通过。最后只删未用 import 和更新两处说明，py_compile 通过，并在候选重跑迁移4项与remediation18项通过；未重复计算通过数。

剩余基线失败明细：behavior 4 项仍断言输入关键词自动产生情绪/亲密度；final_review 2 项仍要求旧 Hold 轮计数及自动情绪；natural_conversation 1 项关系修正失败、1 项等待旧 combined-reflection 回调超时。断言保持，没有跳过。干净 `48ddb59` 工作树复跑相应三文件，behavior 同4、natural 同2、final_review 原3项失败；并行提示词修正后的对照中 final_review 为同2项。额外旧失败的消失不计为本轮修复。不能报告“全仓全绿”。

脱敏分析、命令日志及逐文件结果位于忽略目录 `.local/extreme-cleanliness-20261112/`，包括 `owned-files.json`、`metrics.json`、`isolated-python.json`、`isolated-extra-python.json`、`isolated-ts.log`、`isolated-bili.log`、`isolated-build.log`、`isolated-browser-final.log`、`clean-baseline-results.json` 和 `production-readonly-storage-summary.json`；不将运行数据加入 Git。

## 9. 独立复核与 Plan vs Actual

使用 plan-execution-review 和 async-lifecycle-audit 对照本轮用户 S1–S6。Python/TS 分工不重复改文件；复杂 Journal/迁移与 TS 状态/预算/维护边界交叉独立复核。复核发现初稿 DDL 缺显式事务且 secure_delete 有连接副作用，已改为 SAVEPOINT 和配置恢复，新增失败注入并独立实跑 4/4。

| 原要求 | 实际结果 | 状态/偏移 |
|---|---|---|
| S1 基线与协作隔离 | 安全引用、原差异快照、并行提交识别、精确文件清单及隔离 candidate | 完成；最终集成基底随已提交提示词任务推进，不把它算本轮成果 |
| S2 全仓真实依赖分析 | AST、注册链、配置/构建/部署资产、只读真实四库 | 完成范围调查；非逐分支穷尽证明，未覆盖项见第2节 |
| S3 激进清理与迁移 | 8份生产文件删除、旧接口同步删除、6项职责收敛、派生索引退出迁移 | 代码完成；未直接修改生产数据；实际旧历史与外部维护消费者按证据保留 |
| S4 验证 | 隔离构建、95项TS、288项Python及浏览器；基线对照 | 本轮相关新增回归通过；8项历史失败保留，未声称全绿 |
| S5 高风险独立复核 | 迁移事务/Journal未知提交、在线/预算、维护所有权互审 | 完成；未改变认知权、35轮策略、实际权限或 unknown 重试合同 |
| S6 提交/受控发布 | 本轮代码独立提交、报告归档 | 提交完成；部署条件未满足，未上线 |

有意接口不兼容见第3节；HTTP 收藏版本值由文件原文字节哈希改为解析后内容哈希，旧页面可能一次409，刷新获得当前版本即可，不迁移索引结构或篡改用户编辑。非 running/缺失运行状态明确拒绝是预期合同收紧。未计划的新业务状态、权限、重试和模型判断规则均未新增。

## 10. 生产状态及剩余条件

只读采样：部署记录和容器 build.json 均为 `b41d10881b6bdeeeb94b58cf7a2cd7b0869ed6f4`，0.1.0，Node v24.16.0，manifest `gitDirty=true`；最近 heartbeat 约0.48秒，maintenance=null。四 QQ online/coreReady 4/4，无离线。数据库四个 shared_context active **不能代替实时共享 Worker 组成员核验**。

未执行生产切换，原因是尚未完整确认实时共享组拓扑与维护所有权，线上有源码外控制/热维护覆盖层且 manifest 并非干净源码快照。新安装器也不能自动移除运行中 Agent 已捕获的旧闭包；发布须明确 Agent 生命周期或方法刷新方案，并核对维护窗口、备份和回退。没有重新登录、清空历史、强制压缩或重复发送来制造发布成功。

继续精简的具体条件：回收 `r4-foreground-maintenance.js` 与 control release 的实际消费者；对 available archive/cutover 和有引用旧表逐类确认合法退休条件；补实时共享组与发布来源证据；将剩余旧认知/Hold测试迁到当前真实合同并保持必要语义断言。原始存量存在并不意味着永久保留全部旧机制，也不授权本轮未经验证删除。

## 11. 问题闭环

| 问题 | 根因 | 整改措施 | 证据 | 状态 |
|---|---|---|---|---|
| 废弃检索链和原文派生索引 | 原型遗留，当前 provider 不消费 | 删除两模块；启动事务清理索引/triggers | 调用链、四库计数、迁移失败注入 | 已删除；生产迁移未执行 |
| 重复收藏索引管理 | HTTP/Agent 各自读写和局部锁 | 共同 reader/writer/文件锁 | 8并发编辑、相关测试及浏览器 | 已重构 |
| 在线状态解释漂移 | 管理 SQL 与 JS/执行门各自解释 | 共同 SQL/helper，明确 running | admin/status/预算测试、独立复核 | 已重构 |
| Journal 重复持久化 | commit/unknown retain 复制主体 | 统一 durable adoption，保留许可结算区别 | Journal及失败注入回归 | 已重构 |
| 维护重复 tick 与闭包状态 | 旧补丁重复包裹 canonical tick | 删除 wrappers/hooks，保留动态对象 Owner | 维护幂等/失败回滚/身份测试 | 已删除补丁 |
| 原型接口与人工实验负担 | 无现行调用或旧固定实验依赖 | 删除未用协议/API/脚本，同步调用方和测试 | AST、入口链及部署消费者调查 | 已删除/有意不兼容 |
| 前端传输旁路 | 收藏独立 fetch 错误处理 | 使用共同api，保留取消及写入未知语义 | 类型检查、浏览器、api合同测试 | 已重构 |
| 真实旧 Journal/恢复存量 | 有效不可变历史和可用恢复记录 | 保留最小读取，不改数据 | v1/v2、archive/cutover关联计数 | 仍必要 |
| 全部热维护可退役 | 实际存在源码外生产消费者 | 保留安装入口，删除其内部重复补丁 | 线上脚本调用点 | 仍必要；完整退役条件未满足 |
| 全仓测试/生产完全收口 | 旧合同断言与不完整发布来源 | 明列8项基线失败及部署条件 | 干净基线复跑、manifest/实时探针 | 未完全收口，未伪报通过 |
