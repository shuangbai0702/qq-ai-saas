# FishBot R4 前台生产应用与验收记录

本记录更新此前《FishBot-R4-frontend-implementation-20261107.md》的“待部署”状态；保留旧报告作为当时证据，不将旧测试冒充新版本验证。**主版本和图片兼容修正已应用；未宣称完整原始R4计划所有验收完成。**

## 实际版本和生产范围

- Linux基线 `4f22bd5`，候选基点 `089419f2bc8a90459da7310b3766406cfb223641`，叠加本地未提交变更。按用户最新要求，不再提交或推送Git。
- 主包 `f3c830e73eb4bc957b6c8385037d96b0628787fc04fe7e80db1464d31d7c8c07`：23个源码/构建文件。
- 图片兼容包 `68e8ac61723e17471167f631fe2075f0886c68a8d3c4204db232daf987708dae`：仅 readonly_journal.py，SHA256 `d7aeba53d409e1bfa80542b09575a4c5d0057c9daa70bdd50ea1b8e59face851`。
- 完整文件摘要见同目录生产manifest。没有部署整个脏工作区。Linux运行目录为 `/opt/fishbot-pg-cutover-20261031/app-final-b`。
- 主包只重启API容器；Agent原进程通过现有维护入口更新context传输，分别在空闲边界替换两个AI worker。图片修正只再加载两个AI worker，不重启API。
- 四个QQ协议进程的PID和创建时间均保持原样：1915900、1916089、1916172、1916376。Agent PID 1915835及创建时间保持原样。四机器人状态均online、protocolOnline/alive、aiReady=true。
- 未主动发送模型或QQ测试请求，未切换模型/凭据。原自主协调器临时等待空闲暂停后恢复同一对象；未整改外层 autonomous_runtime.py 或 decision_history.py。

## Plan vs Actual

| 原要求/后续修正 | 实际实现与证据 | 状态 |
|---|---|---|
| 保护原工作区、以Linux为基准 | 独立worktree；生产源码及编译逻辑对照；23文件精确清单 | 完成 |
| 35真实业务轮、一次许可 | memory_reflection.retry事务签发精确35轮证明；候选reserve/settle；大合并/失败不签发 | 隔离测试通过 |
| 周期内System与旧wire不可变 | SystemContract、ReadonlyJournal、最终HTTP边界验证；网页保存需后端认证审计 | 实施并在线抽样验证 |
| 工具合法变化、身份实时 | 实际工具列表不冻结；动态平台事件资料尾部追加，后端继续真实授权，不使用developer | 隔离验证通过；模型身份理解长期质量未评估 |
| 普通失败/取消不清基线 | abort只撤候选；serial/epoch/依赖防迟到；快照和许可消费原子事务 | 单元、barrier与子进程故障测试通过 |
| 进程重启不绕过护栏 | 私有runtime原SQLite有界快照；PID@birth识别死候选；损坏/丢失明确拒绝 | 隔离故障测试、线上worker替换前后快照验证通过 |
| 旧历史不可恢复可放弃 | 依用户后续授权，显式一次cutover；首次快照原子消费；图片修正未再次登记 | 完成；不是常规重构例外 |
| 简短增量、有界容量 | 相同policy不重复；只读状态精确delta；来源/正文相同的共享表达沿用现有管线 | 选定语义还原测试与真实材料35轮重放通过 |
| 正常英文不误删、发送一致 | 去掉启发式英文过滤；明确NO_REPLY、空文本、独立reasoning、媒体ACK终态 | 实际发送包装器使用替身协议验证通过 |
| 完整真实请求35轮/质量/费用/长期多租户 | 只有历史native材料重建、无全部原始HTTP/ACK；未做真实请求探针或生产A/B | 部分，不能称全部验收 |
| 生产部署 | 后续用户明确授权后执行；原“不部署”限制已被后续授权更新 | 已执行 |
| 外层35规划周期 | 用户明确暂缓；不纳入本次整改 | 未实施，符合范围 |

## 新增修正与函数

- `memory_reflection.py:reconstruction_stamp/context_dependency/reserve_context_reconstruction/settle_context_reconstruction`：正常召回摘要集合变化不签发重构许可；成功检查点水位与遗忘epoch决定退役。已选摘要全文、来源和覆盖仍参与await栅栏。
- `behavior.py:prepare_chat_payload/settle_journal`：媒体异步处理前后检查同一依赖；普通召回摘要变化尾部追加，旧候选不得覆盖更新基线。
- `readonly_journal.py:_persist/prepare/commit`：快照、cutover消费和重构许可消费同一SQLite事务。容量失败明确报告，不提高384K/256K等边界。
- `system_contract.py:request_identity/abandoned_request` 与 model_cache 请求ID：死进程预约可恢复；活进程/未知所有权不得释放。不是用重启获取重构许可。
- `readonly_journal.py:same_native_message/same_native_prefix`：SDK会在当前动图加入固定temporary说明，但保存native历史时省略。仅当真实来源、user角色、所有其他字段及图片URL/字节完全相同，识别该固定输入视图差异。**旧wire中的说明、图片、正文均不删除、不改写。**未知说明、图片变化、身份变化、正文变化仍拒绝。

## 实际测试与体积

- Windows与Linux隔离SDK环境，各 **215项Python测试通过**，无跳过或削弱断言；Linux退出码0。包括真实子进程死亡、旧epoch、取消、媒体说明正反例、30轮SDK窗口下35轮旧wire精确断言。
- 主版本真实foreground与send包装器在Windows/Linux使用替身模型和协议通过；Node/API/维护13项通过，TypeScript noEmit通过。最后图片补丁仅改journal输入视图识别，215项全量受影响套件已复验。
- 35轮材料来自同一会话55个来源事件、53个完整native业务片段及15个保存HTTP请求。**不是恢复全部35个原始HTTP请求，不提供完整ACK证明；历史状态取最近首次观测快照。**
- 19个重叠35轮窗口，全部已提交wire前缀精确不变；最大wire373909字符、text283984字符；未触及现有边界。某个35轮窗口初始127077字符，35轮306902 wire/269751 text字符。
- 选定窗口Python tracemalloc峰值约1.8MB，重放约569ms；这不是生产RSS、端到端延迟或模型延迟。未将字符除以3冒充Token；重放actualTokens=null，模型请求/QQ发送均0。
- 容量样本不足以证明所有流量永不阻塞；费用、长期质量及高峰压力门槛仍未完成。

## 线上自然流量与异常

- 主版本早期自然续接出现缓存21504/24842及33152/36020；仅为实际请求样本，不推断总体收益。
- 图片兼容问题曾导致若干请求在发出模型请求前被ContextReconstructionDenied拒绝，未静默重构。修正后已观察到两次自然foreground completed和journal commit，后一次可比旧前缀74消息/179756字符，System SHA不变；不能把历史失败从统计抹去。
- 图片更新前后，两条已存在journal的wire前缀与System hash精确保留；另外两机器人尚无已提交journal。所有原facts、摘要、来源、核心便签、历史轮次均逐行包含于新库；不等于已证明每条事实模型都正确召回。
- 另一机器人曾出现3次上游AuthenticationError；请求失败仅abort，已有journal保持ready。未改变凭据或自动重试外部操作，不能称整体模型服务始终正常。随后最新观测该机器人3次自然请求completed并commit，最新cached86272/input89902（约96.0%）；另一活跃机器人最新cached58752/input61712（约95.2%）。鉴权错误当前未持续出现，原因仍未查明，不能宣称已修复鉴权。
- 上游满前缀返回0、未报告缓存、鉴权错误分别统计；不根据响应ID形状定通道，不宣称解决上游缓存。

## 剩余限制和回退

1. 35轮前立即遗忘与绝不修改旧wire发生硬冲突时，当前明确拒绝继续发送旧材料，不能擅自提前重建。这仍有可用性代价，不能说已完全解决。
2. 关闭记忆/摘要或非35旧配置缺少合法许可时，容量生命周期需另行验收；不伪造网页保存。
3. 无明确标记的分析文本若进入content，不能可靠识别；保留正常英文，不宣称彻底消除泄露。
4. 长时间多租户压力、端到端身份/约定质量评分、全部真实35轮HTTP/ACK、生产费用对照尚未完成。
5. 生产备份/操作回执在 `/control/r4-foreground-20261107` 与 `/control/r4-media-20261107`；源码before、停止相应AI后的SQLite备份、精确manifest均保留。不回滚QQ登录状态，不用恢复旧数据库复活已遗忘信息/已消费许可/外部操作，不自动重发unknown。
6. 单文件图片修正可恢复该发布before源码，再在空闲边界仅重载AI；但会恢复已证实的动图拒绝问题。主版回退需逐项比较新配置审计/许可/已提交快照，不能删除新表或让旧代码重新bootstrap。

证据位于本地私有 `.local/r4-publish-20261107`（原发布、重放、213项原版测试）和 `.local/r4-media-20261107`（单文件发布、215项Linux测试、线上快照/内存包含证明、自然流量）。原始抓包、凭据和数据库不入Git。以上保留了计划偏移与未完成项，不把“测试通过”写作“完整R4全完成”。
