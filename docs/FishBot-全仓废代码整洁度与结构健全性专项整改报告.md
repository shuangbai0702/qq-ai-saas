# FishBot 全仓废代码整洁度与结构健全性专项整改报告

日期：2026-11-12。性质：实际源码整改与提交；本轮未部署生产。

## 1. 基线、所有权与交付版本

- 项目：`qq-ai-saas`，分支 `main`；修改前 HEAD：`a3fd4cc3ba767b1621cbbff5a3ef7ff7eeca1fad`。
- 最终代码 SHA：`8c29f5409a27f0f88a59a4dcae692530bd7a7198`，提交标题 `fix: remove obsolete layers and fence lifecycle ownership`。本报告另作文档提交；报告提交号可用 `git log -1 --format=%H -- docs/FishBot-全仓废代码整洁度与结构健全性专项整改报告.md` 查询，避免自引用哈希。
- 已建立安全分支 `safety/cleanliness-20261112-a3fd4cc`；原有差异、状态及基线 SHA 留在忽略的 `.local/cleanliness-20261112/`。没有将密钥、数据库、QQ 会话、原始采样及测试缓存加入 Git。
- 开始时已有 11 个 Python 文件及一份既有报告修改，还有未跟踪的报告和测试。本轮期间其他维护任务继续修改提示词、认知产品及其测试。它们未被纳入本次 30 个路径的代码提交；提交后仍留在原工作区。
- 已核对近期 `b41d108` 系统收敛、`6a0d759` 群工具分段及相关发布记录。提示词优化当时仍是独立工作区变更，不能用历史报告假定它已提交或部署。

## 2. 扫描范围与证据强度

对 Git 跟踪的 `adapters`、`apps`、`packages`、`scripts`、`docker`、`templates` 建立生产源文件清单：259 个候选源文件，扩展名计数为 Python 108、TypeScript 75、TSX 32、MJS 21、JS 11、SQL 12；不含网页公开设计研究目录。清单包含本次随后删除的六个文件。108 个 Python 模块均可 AST 解析，盘点到 1196 个函数、93 个类；同时发现大量 `getattr` 和动态任务入口，因此没有把静态未引用等同于可删除。

入口核验覆盖 API 创建与关闭、独立 Agent、协议适配器注册、共享 Worker、Python bootstrap、SDK monkey patch、历史查询拦截、配置 lease、压缩退休、Vite 与生产构建。使用 TypeScript 的额外 unused 诊断定位候选，然后按调用方逐项处理；该额外诊断不是项目既定验收门槛，剩余未使用形参及测试诊断没有被伪称清零。

深入调用链和故障验证集中在 `behavior.py`、`bot_pipeline.py`、`context_transport.py`、`readonly_journal.py`、`semantic_runtime.py`、NodeAgent、ProcessHost、热维护入口及 API 媒体上传。其他模块完成入口/依赖和候选盘点，不能据此宣称每条业务分支均已人工穷尽审计。正在被其他任务修改的记忆/提示词文件只作边界调查，未叠加修改。

未递归扫描依赖、缓存、运行数据、私人历史和临时测试沙箱；未审阅第三方 SDK 全量实现，未验证全部旧版本部署组合。PGlite 模板只核验安装结构要求，未读取其数据库内容。搜索按相关目录限制，广域搜索单线程；磁盘活动率 50% 是行为软目标，没有宣称硬性保证。

发布盘点另覆盖 scripts 60、templates 168、docker 5 及 5 个根配置文件的文件名范围；模板资产没有逐件审阅。子任务期间曾误枚举 PGlite 模板数据库文件名，未读取内容或修改，随后立即停止，未计入源码覆盖。

## 3. A 类：实际删除及无用证据

| 删除项 | 确认依据 |
|---|---|
| `apps/web/src/interactive-bg.js`、`interactive-simple.js` | 当前 HTML 入口只加载 `main.tsx`；页面由 `PaperExperience` 安装纸面效果。生产代码、构建、模板、测试和相关设计/说明中无这两个脚本入口；无 glob 或目录式运行时加载。 |
| `apps/web/src/style-premium.css`、`style-refined.css`、`style-ultimate.css` | 当前 CSS 由 `main.tsx` 和 `PaperExperience` 明确导入；三文件无生产或演示入口。保留仍由演示页引用的 `style-final.css`、`style-unified.css` 和 `interactive-enhanced.js`。 |
| `apps/control-api/src/qq-favorite-semantics.ts` | 文件仅两行，纯 `export *`，无权限、格式或错误转换职责。两个生产导入和一个测试导入全部直连既有 `packages/shared/qq-favorite-semantics.ts`，构建和收藏测试通过。 |
| ProcessHost 的 `secrets` Map | 从未写入和读取；真实脱敏使用子进程日志闭包的 `childKeys`。删除空 Map 及只断言它为空的伪安全断言，保留并运行真实跨管道分块凭据脱敏测试。 |
| SharedAstrBotAdapter 的未消费 `context` 属性 | 构造传入的闭包没有消费路径；删除属性持有，保留 `_context` 参数位置以维持现有构造调用合同。没有改变 attach 授权。 |
| Python 局部死逻辑 | 删除未消费的 `ReadonlyJournal`、`person_filter`、`TRUSTED_CURRENT`、`uuid`、`arrange`、`extract_pool` 导入，旧 `intent` 读取，未被消费的 `changed/nonlocal`，以及两条结果相同的分支。保留有真实用途的相邻逻辑。 |
| TS/TSX 和管理员脚本的无用导入、旧配置更新闭包 | 编译器定位后核对实际引用；只删未用符号和未调用的 `update` 闭包，保留组件对外 props 和真实管理入口。 |

没有删除表、历史记录、迁移脚本、工具注册名或持久化字段；没有以静态搜索替代动态入口查证。

## 4. B 类：重复责任收敛

1. 删除 Favorite、Autonomous、Bilibili 三个热维护 `shutdown` 包装及其保存/回退字段。`b41d108` 和当前 HEAD 的 NodeAgent 已动态读取并关闭这三个当前 owner；再次包裹会重复 stop，且在基础停机关闭准入之前留下等待窗口。最终只由 canonical NodeAgent 负责停机，维护函数不再替换其 shutdown 方法。已有 tick 兼容包装继续保留。
2. 收藏语义的 API 和测试直接引用已有共享实现，去除中间转发模块。共享实现、视觉判定、重试预算及人工编辑保护均未重写。
3. SDK 查询结果归属复用现有 Store 和配置对象身份；没有新增第三套租约、历史池或状态机。

PostgreSQL 任务/外部动作账本、SQLite Work/入站、Journal、媒体归属与模型认知产品继续各自承担原职责，没有因为字段相近而合并。

## 5. 异常处理、恢复与资源所有权修复

### SDK 历史查询迟到

旧历史读取钩子先 await SDK，再选择当前 Store，可能把旧查询结果解释为新配置或新 Store 的结果；原 owner 已移除时还可能直接返回未过滤结果。现在在 await 前捕获 Store 与配置 lease，返回后身份变化、移除或关闭则返回空结果。本次是无外部副作用的只读查询，当前绑定可以重新查询，不设置永久失败状态。稳定 owner 仍在 await 后读取最新退休 tombstone，避免查询期间已退休原文复活；没有修改已提交 Journal。

### NodeAgent 停机和迟到准入

- 原来某个维护 owner 的 `stop()` 抛错会跳过其他 owner、协议子进程及 lease 清理。现在继续向其余 owner 发起停止，保留首个错误，执行既有 ProcessHost 身份校验兜底。
- 并发 shutdown 调用共享同一 Promise；第一个 await 前关闭准入并停止定时 tick。两处数据库查询返回后再次检查 closed，阻止迟到命令、登录轮询或健康任务启动。
- fallback 清单读取失败、同步 stop 抛错及异步停止失败均进入 failure，其他可独立停止的进程仍有清理机会；无法确认资源已停止时不删除 lock、不释放 lease，也不返回成功。全部确认后才释放所有权。
- 独立复核实际复现了旧维护包装在慢 stop 期间接纳新命令的问题，最终通过删除冗余包装收口，而非另加一个状态系统。

### 图片上传回滚

原代码在 `writeFile(..., flag:'wx')` 遇到 EEXIST 后仍设置 `installed=true`；元数据提交失败会删除并非本次创建的图片。现在只在独占创建成功后取得回滚删除权。故障测试证明：借用已有图片时保持字节不变；本次新建图片在失败时回收；临时元数据均清理。没有外部平台动作，不涉及 unknown 重放。

## 6. C/D 类：明确保留

- Coverage/Hold 命名下仍被旧 Journal 恢复、原始窗口核对和历史许可解析使用的代码保留；正常 semanticBatch 合法退休路径不要求 Coverage。本轮没有恢复旧 Hold 机制，也没有增加原文完整性限制。移除条件是旧持久化格式真实不再使用且恢复测试有替代证据，不能只看名称。
- A/B 混合历史、旧正文 decode、未完成 Work、真实 unknown、有效约定、事实与关系均保留。
- `emotional_phrase`、`owns_reply` 存在真实调用；`reserve_interjection` 仍被兼容测试调用。后者是否还有部署侧反射消费者证据不足，未强删。
- `scripts/napcat_login_patch.py` 是独立 Agent Linux 启动期以及相关 setup/build 的真实幂等补丁入口，保留。
- 仍被演示页使用的旧视觉资源以及公开设计研究/许可资料保留；没有把历史设计资产等同于生产死代码批量删除。
- 依赖未找到足够安全的移除证据，`package.json`/锁文件保持；SQL 迁移兼容保留。本轮没有依赖数量下降的虚假成果。

## 7. 实际复杂度变化

代码提交共 30 个路径，250 行新增、2062 行删除，净减少 1812 行，包含回归测试；其中五个未使用视觉文件共 2009 行。行数仅描述结果，主要收益是六个生产文件退出维护、三个停机包装及其影子责任移除、一个纯转发模块退出、一个无用 Map 和一个无用闭包属性退出。NodeAgent 仅增加一个局部 shutdown 实现和一个共享 Promise，没有增加服务、调度器、发送账本、原文池或通用状态机。

## 8. 验证与可追溯性

为隔离并发维护，在 `.local/cleanliness-20261112/candidate` 建立基于原 HEAD 的独立工作树，只复制本次 30 个路径。依赖及 SDK 通过本机既有目录链接提供，没有带入其他任务的源码修改。提交后逐路径比较最终 Git blob 与候选，除规范化 CRLF/LF 外全部一致；删除项亦一致。

| 实际执行 | 结果 |
|---|---|
| Python：context_transport 30、readonly_journal 27、bot_pipeline 13、semantic_runtime 28、semantic_archive_recovery 12、native_conversion_ownership 2、semantic_native_read_ownership 3、semantic_batch_summary 7、semantic_journal_retirement 9、semantic_all_scope_cleanup 11、semantic_cross_scope_merge_guard 2 | 独立候选 144 项通过，11 个脚本均退出 0 |
| Python 补充：context_reading_journal 10、context_reading_checkpoint 1、admin_checkpoint_20261110 12、semantic_discard 7 | 30 项通过；明确覆盖 A/B 混合恢复、管理员授权及正常 35 轮的原文缺失恢复；Python 全绿组累计 174 项 |
| `summary_material_test.py` | 20 项中 19 通过、1 失败；原始 a3fd4cc Python 源码复跑同样失败，见下文基线说明 |
| `pipeline_boundaries_test.py` | 10 个 PASS 场景，退出 0；包含迟到配置、unknown 不重发、Work 有效轮、上下文 slot 释放及记忆 epoch |
| Node：agent-operation-isolation、autonomous-maintenance、foreground-maintenance、process-host、autonomous-lifecycle、shared-lifecycle、shared-recovery、social-media-rollback、png、qq-favorite-semantics、code-review-favorites、qq-favorites、social | 13 个文件，60 项通过，0 fail/skip/cancel；包含真实 SDK + 本地确定性模型 fixture，不发送真实 QQ |
| `npm run build:production` | 退出 0，包含 `tsc --noEmit`、ESM/维护 bundle、Web 构建；65 个 TS 源入口进入构建清单 |
| 代码差异检查及提交候选一致性 | 通过 |
| 独立复核 | Sol 复核 Python 动态入口/lease/退休；另一 Sol 复核 TS 所有权，复现并推动修复慢 stop 准入，最终单例回归再次通过 |

新 SDK 所有权测试对旧实现在内存还原后出现 5 个断言失败、0 errors，修复后通过。原工作区的代理先期测试统计含其他任务新增用例，未冒充本次独立候选计数。

生产构建仍有 Zod 注释和 Web chunk 大于 500kB 的警告，未调低检查强度或改阈值掩盖。候选构建时尚未提交，build.json 如实记录原 SHA 与 dirty；最终代码对应关系由上述逐路径比对及提交确认，而非冒称它当时就是 clean HEAD 构建。

基线失败为 `test_native_view_and_confirmed_delivery_are_distinct_in_complete_batch`：断言要求 `memory_round_material` 的 event 134 仍存在，实际已被成功退休清理。候选与原始 a3fd4cc 的同一测试、同一断言均失败，其余 19 项通过。本次没有修改该测试及其清理规则；该文件也正被独立维护任务修改，未覆盖、跳过断言或回退清理行为来凑 PASS。基线复跑后候选源码逐字节还原。不能宣称全仓测试全部通过。

未运行全仓所有历史实验、真实 PostgreSQL 多连接竞争、长时间自然流量压力或付费模型 A/B。日志与私有候选清单保留在忽略目录，不随 Git 发布。

## 9. 模型自主性、压缩与安全边界

没有修改模型是否说话、参与群聊、联系主人、休息或规划任务的判断规则；没有增加关键词决策，没有修改固定人格、工具授权范围、QQ/B站预算或真实发送状态分类。

34/35 轮边界、管理员强制压缩、原文不完整时的合法压缩、退休后业务与约定保持、旧格式恢复由现有针对性用例回归。没有重写已提交历史，没有失败前清理，没有新增 Hold 或第二原文池。历史查询过滤修复只阻止旧 owner 的迟到结果被当前请求采用。

## 10. 生产部署状态

本轮仅执行只读预检，没有获取维护所有权、运行迁移、更新生产文件、重载 Worker、重启 Agent/QQ、重新登录、发送验收消息或回放 unknown。

真实生产只读探针确认：部署状态 OK，部署记录与容器 `dist-server/build.json` 均为 `b41d10881b6bdeeeb94b58cf7a2cd7b0869ed6f4`，版本 0.1.0，Node v24.16.0；构建记录 `gitDirty=true`，不能仅凭该 SHA 宣称每个生产文件与 Git 完全一致。采样时 Agent 心跳约 0.4 秒，maintenance 为 null。本地旧生产 PID 文件已过时，未拿它作为现行生产证据。

复用现有 NapCat 管理 API 只读状态探针，四个匿名目标 online 4/4、coreReady 4/4，offline/unavailable 均为 0。该探针不返回实时共享组成员数，未将四个数据库当成四个 Worker；实际组归属与配置 revision 的完整一致性没有在本轮确认。生产编译文件中的 canonical shutdown 方法体也未取得可靠定位证据，Git 版本检查不能代替此项。维护标记不等于跨进程全局维护锁。

本地可确认提示词测试、构建及多组 AI 维护仍活跃，源码差异继续变化。不能将整棵脏工作树构建物作为本次发布；另外本次涉及 canonical Agent 停机实现，不能仅加载原有维护 bundle 就声称其已经升级。生产逐文件差异、实际四 QQ 及共享组、维护互斥和更新方式需要在发布时重新核实，因此保留已验证提交，本轮未发布。

## 11. Plan vs Actual 与剩余风险

对照用户六阶段：盘点和候选确认完成；有证据的删除、统一和修复已实施；必要回归、构建、独立复核及代码提交完成；报告交付完成，生产部署未执行。没有把静态盘点扩大成全部分支均已验证，也没有将并发提示词变更纳入成果。

合理收敛：没有机械拆分大文件，没有追求删除数量，没有清理仍有兼容依赖的旧机制；实际高风险变更集中在 SDK 只读结果和 Agent 生命周期所有权。未改变账本边界、数据库 schema 或外部动作重试语义。

剩余限制：失败 shutdown Promise 在同一进程缓存，调用方会继续得到相同失败；独立 Agent 信号入口会退出，原有 lease 过期后可恢复，未新增同进程重试框架。古老且未核实的 Agent 热补丁组合不在本次兼容承诺内；后续发布须核实实际方法来源。对 unbounded cache、循环依赖和迁移废弃候选，本轮没有足够证据可安全删除的部分继续保留，不声明全仓风险归零。既有历史凭据轮换、旧无身份材料归属、长期认知质量等证据缺口不因本次清理而关闭。

## 12. 问题收口表

| 问题 | 根因 | 整改措施 | 证据 | 状态 |
|---|---|---|---|---|
| 旧视觉实现留在生产源目录 | 已被当前明确入口替代 | 删除五文件，保留演示依赖 | 入口/引用/动态加载核验，生产构建 | 已提交 |
| 收藏语义多一层无职责转发 | API 外壳只 re-export | 直连既有共享实现，删除模块 | 收藏语义、编辑和权限回归 | 已提交 |
| 空秘密 Map、无用闭包与局部死代码 | 旧迁移残留，无消费方 | 删除无用持有、导入、状态及同效分支 | 调用核验、真实脱敏及邻接测试 | 已提交 |
| SDK 旧查询套入新绑定 | await 后才选 owner | 预绑定并复核 Store/配置身份 | barrier、期间退休及重新查询测试 | 已提交 |
| 停止异常跳过其他资源 | 顺序 await 无故障收口 | 继续清理、抛错、未确认时保留 lease | owner/fallback/清单故障测试 | 已提交 |
| 停机迟到与重复维护关闭责任 | DB await 和包装 stop 留有准入窗口 | await 后查 closed；删除三 shutdown 包装 | 慢 stop、迟到 DB、方法身份、共享隔离测试 | 已提交 |
| 上传失败误删已有图片 | EEXIST 被误记为新建 | 创建成功才取得回滚权 | 已有/新建两类元数据故障测试 | 已提交 |
| 旧恢复及历史兼容候选 | 仍有持久化/恢复/演示依赖 | C/D 类保留，等待明确移除证据 | 旧格式、恢复与退休回归 | 明确保留 |
| 原始材料保留的旧测试断言 | 成功退休规则与旧测试预期不一致 | 不覆盖并发维护、不回退退休；单列基线 | 候选和原始 a3fd4cc 均同一断言失败 | 基线未收口 |
| 生产发布与其他维护并发 | 活跃源码修改、运行版本需精确比对 | 隔离候选验证，保留提交不混合发布 | 进程、真实生产只读探针、候选 blob 核对 | 未部署 |
