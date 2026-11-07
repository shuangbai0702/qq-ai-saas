# FishBot 整改执行工作日志（主智能体维护，2026-11-07）

本文件是执行期工作日志，最终内容并入《FishBot-全系统整改实施与验收报告-20261107.md》。注意：`_` 前缀为工作文件，不属交付报告。

## 0. 阶段0结论（已完成）

### 三轨合并结果
- 线上生产 = `/opt/fishbot-pg-cutover-20261031/app-final-b`，23文件与 `docs/FishBot-R4-production-manifest-20261107.json` 逐字节一致（SSH只读核验 2026-11-07）。
- `readonly_journal.py` 线上为图片修复版 SHA256 `d7aeba53d409e1bfa80542b09575a4c5d0057c9daa70bdd50ea1b8e59face851`（含 `_SDK_MONTAGE_NOTICE`）。
- 工作树已对齐生产：23 manifest 文件全部字节一致；另从 `.local/r4-publish-20261107/worktree`（R4会话最终态，mtime 00:43~01:48）采纳55个文件（含 tests/montage 用例、journal_cutover.py、autonomous_runtime.py/decision_history.py 的 R4 会话收尾修改）；6个 dist 从线上只读下载恢复。
- 恢复前工作树旧版本备份于 `.local/baseline-pre-restore-20261107/`。
- R4 分支 `codex/fishbot-r4-foreground-20261107`（=main+3 commits: 4f22bd5/a4ca20f/089419f）保留未动；未提交、未推送任何 Git。
- 恢复后 `python -m unittest tests.readonly_journal_test tests.system_contract_test` 32项通过。
- 容器状态（只读）：fishbot-production-api/agent healthy，image `0.1.0-security-r3`；2个群bot shared-worker（01:53/01:54启动=媒体修复时段）。
- 部分bot无Journal表原因：journal 在该bot首次成功35轮检查点提交时创建；当前2群bot有已提交journal，另2bot尚无。属正常状态非缺陷。
- 阈值现状：decision_history.py `MAX_TURNS`(=summaryRounds=35)、`MAX_ADDED_CHARS=256000`、`MAX_HISTORY_CHARS=160000`、`MAX_RETAINED_TEXT_CHARS=384000`、`MAX_RETAINED_WIRE_CHARS=2MB`；journal 存储 `MAX_PERSISTED_JOURNAL_BYTES=16MB`、`MAX_PERSISTED_JOURNALS=64`、`MAX_JOURNAL_TOMBSTONES=4096`。96K→256K的依据=R4发布记录"增长以基线为界有界放开"，未擅自再扩。

### 服务器访问方式（复用现成模式）
- paramiko via 本地代理 127.0.0.1:7897 HTTP CONNECT → root@110.41.11.66；凭据从 `C:\Users\76868\Desktop\ps.txt` 运行时读取（参考 `.local/r4-media-20261107/stage_release.py`）。paramiko 系统可用(5.1.0)，也可用 `.local/ssh-deps`。
- 纪律：只读；生产写库/重启/真实发送/部署一律另获授权。

## 1. 架构机制速查（主智能体已核实）

### 前台（R4已部署）
- `context_transport.request_context_view`：把 system+固定GUIDANCE作为稳定前缀；本轮事件包 `EVENT_CONTEXT_CURRENT`(authority/policy/currentGuidance/meaning) + 只读包 `READONLY_CURRENT`（READONLY_FIELDS 字段全集）插入末尾。
- `ReadonlyJournal.prepare`（readonly_journal.py）：扫描历史找 readonly/event 包 → base=去掉这两类包的消息 → `same_native_prefix` 判定 suffix 复用 → `changes(old.state,state)` 计算 readonlyChanges 追加 → event 包仅在与 old.authority 不同时追加（compact_policy 时同 policy 去重 policy 字段）→ 容量边界 `history_boundary`(MAX_ADDED/MAX_RETAINED_TEXT/MAX_RETAINED_WIRE) 超限时需 reconstruction permit，否则 ContextCapacityExceeded。
- `changes(before,after)`（decision_history.py:54）：**索引级列表diff，滑窗位移→整段重发，P0-A主根因**。ApplyChanges 协议 `[set,path,value]/[remove,path]`，数组末尾下标=追加。
- journal.commit 由 behavior.settle_journal 在真实ACK后调；summary 变化由 `context_dependency/visible_summary_ids` 栅栏处理（guarded 模式下 summary_changed 不失效，尾部增量追加）。
- 前台调用点 behavior.py:1226 `journal.prepare(..., strict=foreground, compact_policy=foreground, guarded=foreground)`。
- 重构许可链：memory_reflection.reserve_context_reconstruction / memory_reconstruction_checkpoints 表（round_count=35, state available→reserved→consumed）→ readonly_journal._reservation。前台已闭环。

### 外层（P0-B/P0-C 目标区）
- `autonomous_runtime.py`：orientation/planning 用 `AppendHistory`（decision_history.py:236，内存态，peer 复用 line 504-518），无持久化、无35周期许可。
- Postgres: `autonomy_cycles`、`autonomy_settings` 已有（packages/database/autonomous.ts），finish 幂等编号尚未实现，无 `context_cycle_seq`。
- D7 设计：autonomy_settings 扩展单调 context_cycle_seq + 检查点JSON；autonomy_cycles 记录有效序号；finish 锁行分配序号；35序号=检查点（id/bot/tenant/throughSeq/35cycleId/digest）；orientation/planning 各以 checkpointId+phase 消费一次；快照与消费同事务（bot私有runtime SQLite）。
- 有效cycle定义（§3.1.3）：同cycle有合法planning决策+持久提交+完整反馈结算+未失效；纯orientation/抢占/超时/重复finish/轮询不计。

## 2. 多智能体文件所有权（防冲突）

- 主智能体：decision_history.py、readonly_journal.py、behavior.py、context_transport.py、context_protocol.py、tests/decision_history_test.py、tests/readonly_journal_test.py、tests/context_transport_test.py；全局集成、P0-A、最终验收。
- 子智能体A（D1-D4）：memory_scope.py、memory_reflection.py、hosted_memory.py、companion_insights.py、data_admin.py、memory_search.py、semantic_context.py、summary_material.py、packages/contracts/autonomous.ts、tests/structural_memory_test.py 等。
- 子智能体B（D6-D8+P0-B/C）：autonomous_runtime.py、autonomy.py、initiative.py、participation.py、companion_state.py、companion_insights.py(与A协调)、meme_reactions.py、favorite-maintenance.ts、qq_favorite_tools.py、apps/node-agent/src/autonomous.ts、packages/database/autonomous.ts。
- 冲突文件（companion_insights.py、apps/node-agent/src/autonomous.ts、packages/database/autonomous.ts、bot_pipeline.py）：按批次串行，主智能体合并。
- bot_pipeline.py 由主智能体统一改（A/B 提需求）。

## 3. 测试入口（计划§6指定）
- Python定点：`python -m unittest tests.hosted_memory_test tests.shared_memory_test tests.summary_material_test tests.core_note_test tests.companion_features_test tests.expression_sharing_test tests.readonly_journal_test tests.system_contract_test tests.model_cache_test tests.context_transport_test`
- 独立异步脚本：tests/foreground_prompt_test.py、tests/send_lifecycle_test.py 单独执行；context_crash_test.py 若存在必须跑。
- Node：`npm test`（串行）+ `npx tsc --noEmit`。
- 新增用例并入同入口；不许删测试/降断言/伪ACK。

## 4. P0-A 实施要点（主智能体）
- 根因：`changes()` 对 list 纯索引 diff；`recent_messages/conversation_timeline/group_round_history/recent_burst` 等滑窗/增长列表每轮位移→大量 set 全量重发；timeline 增长同理。
- 方案：稳定身份列表 diff（source_event/event_id 优先，内容hash兜底），输出最小 ops（头部remove+尾部append+原位set）；保持 `apply_changes` 可验证等式不变；**只影响未来新增，不改已提交wire**。
- EVENT包重复：authority!=old.authority 即追加包；需要区分"真新信息"vs"重复包装"；policy 去重已有(compact_policy)，扩展到相同 authority/currentGuidance 判定。
- 无变化字段重复包装：changes() 对 dict 已按key比较，问题主要在 list 位移引发的连锁 set。
- 约束：绝不改 committed 历史；diff 只用于增量；保序、保源、可追溯。

## 5. 待办队列
（子智能体报告到位后填充 D1-D8 细化清单）

## 6. 最新审计+追加计划裁决（两份摘要已固化 .local/audit-digests/）

### 执行框架（追加计划§1，效力最高）
- 顺序：H0版本冻结 → H1失败夹具/权限barrier/入站完整性 → H2无损增量/容量门槛 → H3外层35周期许可 → 原计划其余（D4/D6/D8等）。H4故障调度/费用口径可在H1后并行。
- 审计5项提案已否决：summary_changed自动许可、遗忘应急许可、全bot重做cutover、整体重启、动态policy全升System。
- 产品策略默认值不动：dailyTokenBudget/退避封顶/blocked次数=策略选项，不埋默认。

### P0三item归位
- P0-1(外层接线)：现工作树 namespace 已赋值(:511)——审计针对旧主树，阶段0已消除。剩余=P0-B/H3本身：外层AppendHistory是静默重建(reset_reason记录)，需真实35有效周期许可+原子持久化恢复。
- P0-3(增量退化)=H2=主智能体进行中：identity diff 方案符合"优先试现有set/remove"要求；全协议换record字典需新System说明=许可冻结，不做。
- P0-2(外层成本)=H4：usage对账/错误分类隔离(provider/model/config revision)/心跳单写者串行(main.ts:47唯一temp+顺序)；不擅自设预算/频率。

### H1新增关键夹具
- behavior.py:624-632 batch>20 pop(1) 入站事件移除——潜在P0信息完整性，需追踪第21/22条终态。
- TOCTOU barrier：qq_actions.execute:144-149/group_actions.send:117-151已有检查保留；确定性barrier证明，无真实越权不宣称修复线上漏洞。
- 21/22条突发消息：每真实event原文/媒体/身份/顺序最终可见，不伪标combined。

### 前台实测基线（审计口径，验收对照用）
- readonlyChanges 2.2-13k/轮；C类语义重复55.3%(28650/51783)；wire 379,955字节；距256k增量上限~6k；前缀保留99.97%。
- 容量合同：MAX_RETAINED_TEXT_CHARS=384000、MAX_ADDED_CHARS=256000(8/3≈2.67于96K)、MAX_RETAINED_WIRE_CHARS=2Mi、快照16MiB/64journal/4096墓碑。
- 现有双条件 prompt_cache_key（gpt-* + host白名单）保留；跨cycle缓存断裂=H1诊断项(本地首差异与上游0分开)。

### 验收硬门槛（§6）
- 三组对照：基线/仅record共享/仅EVENT修复/组合，逐项比较不保留零收益复杂度。
- decode(encode(state))深度相等；旧wire逐字节相等；protected完整。
- 前台35轮+外层35周期全程无容量阻断，否则H3不发布；≥3周期、多tenant两worker、取消/重启/权限/媒体/ACK联合回归。
- A→B→A必须再传A（禁全历史hash集合"以前见过就不发"）。

## 7. 主智能体已完成实施（截至本日志更新）

### H2/P0-A identity diff（完成）
- decision_history.py：`changes()` 列表分支= `_index_changes`（原语义）与 `_list_changes`（稳定身份：source_event/event_id/round_event+source_scope，`id` 次级；SequenceMatcher匹配）双路按编码体积取小；中插不可表达时返回None回退索引路径；apply_changes 仍是唯一验证器。**协议[set/remove]未变、GUIDANCE未动、committed wire未动。**
- 测试：tests/decision_history_test.py 新增6条（滑窗位移/原位更新/timeline增长/头插回退/scope区分/随机records roundtrip）。
- 证据：.local/h2-replay-20261107/ — ①真实材料19窗口全过（前缀等式逐轮断言，每窗口maxWire -1,076一致改善，SUM 6,444,444→6,424,000）；②控制轨迹35轮 readonlyDelta 88,812→37,858 (**-57.4%**)，稳态每轮 ~3,000→~1,000。
- behavior.py 诊断扩展：layoutVersion/journalScopeKind/committedPrefixMessages/committedPrefixSha/permitKind/candidateAppendChars。

### H3/P0-B 外层35周期（完成）
- 新文件 adapters/astrbot/outer_journal.py：`PersistentOuterHistory`（独立类）——bot私有SQLite持久化（fishbot_outer_journals/fishbot_outer_permits/fishbot_outer_rebaselines，sha256完整性，16MiB快照上限）；**idle/turn/summary 静默重置全部移除**；遗忘=memory_epoch_changed→需检查点或网页rebaseline证明；binding_changed=显式retire+重基线；容量=OuterCapacityBlocked显式阻断（不静默重建）；检查点 digest 校验（checkpointDigest=sha256({scope,checkpointId,throughSeq,cycleIds}) 规范JSON）+ 每相位一次消费；abort不消费；crash恢复校验；web rebaseline=每相位一次消费。runtime以blocked结果干净结束周期（不crash-loop）。
- autonomous_runtime.py：invoke_decision 换用 PersistentOuterHistory（db=st.db，scope=json['outer',tenant,bot,generation,revision]）；retirement='outer-mem-epoch:%d'%reflection.epoch()（普通摘要写=数据delta）；apply_policy 提取 contextCheckpoint/outerRebaselinePermitId；peer seed_from 共享已提交前缀。
- Node/PG：migrations/011_outer_context_cycles.sql（additive：context_cycle_seq/context_checkpoint/cycle_seq+索引；已注册 index.ts 迁移清单）；packages/database/autonomous.ts `recordEffectiveCycle`（finish事务内：有效周期=有planning决策或wait决策+无fault+无未结算action；单调seq；35倍数铸造检查点；幂等）+ `outerRebaselinePermit`（audit_events config.saved authenticated_web_config）；apps/node-agent/src/autonomous.ts policy() 投递 contextCheckpoint+outerRebaselinePermitId。
- 测试：tests/outer_journal_test.py 10/10；tests/autonomous-coordinator.test.ts 新增"outer 35-effective-cycle checkpoints"测试（34不铸/35铸造+摘要可重算/policy投递/故障不计），套件48/48；tsc --noEmit 0错误。
- 死锁规避：遗忘早于35周期时，认证网页保存证明可重置基线（真实用户动作=合法许可，先例=前台 systemChangePermitId）。

### D5 主智能体部分（完成）
- context_transport.social_context_view：新增单一 observedAt 时钟锚（替代每行陈旧age重发；at保留绝对值）。
- context_transport.experience_view：objective/reason/summary上限600（上游authoring界内完整保留，归属限定语不再被截断）；unfinished 200/detail 240；保留 brief_v1 标记+不增长断言。
- bot_pipeline.experience_context：主人私聊 bounded briefing（未完/unknown/partial=protected全保留+协议回执，其余收敛最近2条completed）；P1-1。
- P1-2 四路经历：按追加计划属合法不同视角（feedback/recentActions/autonomous_experience/execution_experience），请求级动作ID去重列为D5剩余子项（未实施）。

### 遗留测试修复（发布基线漂移，非回归）
- 测试对齐已部署合同：behavior_test（NO_REPLY精确标记+嵌入不误删）、decision_wire_test（36轮跨MAX_TURNS=35、300K跨MAX_ADDED=256K）、decision_context_test（MAX_APPEND_IDLE_SECONDS/MAX_TURNS常量化）、social_test（SELECTION_RULE现行措辞）、experience_context_test（D5新上限）、context_transport_test（observedAt）。
- 恢复已验证测试版本：companion_features/expression_sharing（证据树未验证编辑→回滚pre-restore版，证据版存 .local/evidence-test-drift/）。
- 脚本级测试现状：autonomous_pipeline_test=外层区域断言漂移（H3重构后待修，多数断言需对齐PersistentOuterHistory）；fish_followup_sdk_test（below 80%）、sticker_native_test（IndexError，子B D8范围）；reasoning_effort_test/shared_boundary_test=需argv/stdin由Node harness驱动（非坏）。

### 待办
- 子A wave2（D4）报告→集成（进行中）。
- 子B已交付（见§8）→集成项：control-api qq-favorites sync raw-text绕过（主）、behavior.py:965 sticker清理（主）、companion_insights.py:75 motive作者接口（子A wave2后）。
- sticker_native_test/autonomy-runtime.test.ts 需静置复跑仲裁。
- 前台35轮完整重放（真实材料19窗口已过；准备联合验收记录）。
- Windows/Linux全量、D9 dry-run脚本、验收报告+JSON。

## 8. 子智能体B交付（H4+D8+D6非behavior，已合流到工作树）

- D8: favorite-maintenance.ts 重写（rawText不启用/uncertain≤3持久化/pressure只延期/hash匹配才发布/旧raw-text边界降级/只读诊断）；qq_favorite_tools.py item()禁选修正+prepare幂等(already_prepared/already_sent/digest)；qq_favorites.py descriptions透传。
- H4: agent.ts HeartbeatWriter（单写者/单调seq/唯一temp/原子rename/旧writer不覆盖）；main.ts接入；autonomous.ts charge按(requestId,kind)幂等+source分类(actual/estimated/unknown)+reserveUnknown保守计费；classifyFault(auth/rate_limit/protocol/network/contract/identity/decision)；可选isolation默认关闭；database finish记fault上下文+consecutiveFaults。
- D6: participation.py engaged→中性replied；companion_state motive_record/motive_evidence作者分账（human_request/bot_proposal/model_plan/unknown+acceptance）；companion_schedule model_plan；social.expression_sent自用不加分；qq_actions回执文案中性化；initiative.sweep_reason预算原因透明（budget_exhausted/no_candidates/quiet_hours/disabled）；autonomy failure_class+send_capacity。
- 测试全绿：favorite-maintenance 10/10、heartbeat 4/4（新）、coordinator 48/48（含usage对账/分类5新）、autonomy+self_interest 60/60、expression_ledger 3/3（新）、social_protocol_contract 28/28、companion_features 16/16、tsc 0错。
- 明确未改默认值：dailyProactiveLimit=10、isolation默认undefined、无新预算数值。

### H1 入站完整性（主智能体完成）
- behavior.py batch>20：pop(1)丢弃路径改为 flush-on-overfull（batch_flush事件触发整批立即封口），21条全进单一Work，无一被丢弃标combined；store加batch_flush容器。
- 前台21条突发回归夹具（tests/foreground_prompt_test.py）：并发21事件→单批封口+恰好1个carrier+其余全部batch_combined合并；「到达模型」半边受scheduler桩限制记为待办（代码路径由既有集成测试覆盖）。
- 权限TOCTOU barrier：现有current/attempt检查保留（qq_actions.execute:144-149/group_actions.send:117-151），无真实越权复现→不冒称修复线上漏洞（追加计划裁决）。

### 遗留裁决记录
- P1-2四路经历=合法不同视角（追加计划H2-6），请求级动作ID去重=未实施子项。
- sticker_native_test失败=在途改动交叉（子B stash对照证明非其引入）；autonomy-runtime.test.ts负载敏感，静置复跑。
- 子A的群small摘要跨scope裁决：保持"bot共享背景"语义（两条钉死测试），更严收窄需主智能体决策——维持现状（符合主计划"保留合法群私互通，不一刀切禁跨scope"）。

## 10. 子智能体A wave2（D4）交付与主智能体收尾合并

### D4（子A wave2，已交付）
- summary_material.py：claim层（6类kind、speaker/来源/protected、32KB/96条/240字有界预算、protected优先、parents继承链、coverage增加native-only不签完整覆盖、material/rounds释放门）。
- memory_reflection.py：memory_summaries/memory_core_notes 加列 schema_version/claims（v1不强造）；update_core_note/merge/摘要提交全具名列v2；big前置门——protected装不下不跑模型、记录summaryProtectedConflicts并退避。
- data_admin list带schema_version/claim_count；9个新测试（tests/d4_claims_test.py），144项套件OK。
- 两个基线缺陷上报已由主智能体裁决（见下）。

### 主智能体收尾合并（子A wave2后）
1. long_memory_test/语义召回排序：不降断言，真修——`memory_reflection.recall` 与 `semantic_context.recall` 改为**查询命中先于rolling背景**（可见性不变、相关性提升）；long_memory 17测试绿。
2. autonomous_pipeline_test 修复至 EXIT=0（37阶段全过）：NO_REPLY等已部署合同断言对齐；「取消不清历史」按H3合同重写（前缀逐字节保留+无pending许可+retire语义）；P1-1 keep条件修正（blocked等非completed均protected）。
3. motive作者分账接口落位：companion_insights motives写入改用 companion_state.motive_record（bot_evidence→bot_proposal、user→human_request、acceptance='none'待effects证据）。
4. plugin_alignment/companion_upgrade 的 ModuleNotFoundError=调用约定产物（bare import 仅在discover/direct下可解析，unittest包式调用不注入tests/路径）——非缺陷，记录仲裁结论。

## 9. 中期全量回归（主智能体合并后）
- Python discover：763测试，8个loader错误=脚本式测试设计使然（foreground_prompt/send_lifecycle/reasoning_effort/shared_boundary/autonomous_pipeline/sticker_native/pipeline_boundaries/fish_followup_sdk，各自standalone或经Node harness运行）；真失败仅 long_memory_test（子A wave2在途文件，待其报告）+ decision_wire historical_brief（D5上限连带断言，已修→21/21绿）。
- discovery顺序敏感：context_crash/media_separation standalone绿、discovery中偶发——测试隔离敏感项，非产品回归；复跑仲裁记入报告。
- Node核心：favorite-maintenance 10+heartbeat 4+coordinator 48+d1-evidence 4+social-protocol-contract 28+autonomy 54=68/68；tsc --noEmit 0错。
- D9：scripts/d9_legacy_migration_dryrun.py（只读URI、零写入、fixture测试PASS）。
- 集成完成：qq-favorites sync导入raw_text化（TS）、behavior sticker发送后释放prepare、历史brief断言更新。
