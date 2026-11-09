# FishBot 综合审计复核与系统性收敛实施报告

日期：2026-11-10。依据《FishBot-全系统综合审计报告-20261110.md》、当前 AGENTS.md、实际源码、隔离测试及生产只读取证。以下区分代码验证、加载验证和真实业务验收，不将三者混用。

## 1. 交付结论

**本轮确证缺陷已修复、提交、推送并受控部署；不能据此宣布所有历史故障及性能问题均已解决。**

- 修复群聊等待被错误升级为 `blocked_unknown`、延迟事件重新认领缺少主事件账本所有权，以及已结算 joined 输入可能被延迟入口重新激活的问题。
- Presence 只有真正看到完整原文、作出有效且仍然当前的“不参与”判断，才可结算对应输入；截断、遗漏媒体或缺少原文证据时不永久忽略。
- 摘要不再每批重复携带全部旧 Hold；复用既有有界恢复流程。相同事件、相同作者、内容完全相等的重复正文只在模型资料侧去重。
- 摘要专用网络读取预算改为180秒、外层185秒；前台及其他判断预算不变。增加不含正文的失败抛出位置诊断。
- 内存控制器诊断日志改为50MiB轮转、保留两代，不改变软限、回收算法、QQ状态或 Journal。
- 两组 Worker 换载完成，4个QQ在线、AI ready，NapCat协议进程及创建时间保持；1554条部署前历史消息和System向量哈希验证通过。
- 本地清理回收约162.8GiB逻辑数据；发现并披露一个旧发布辅助目录误选，已恢复服务器可取回部分，未伪造丢失的旧本地测试日志。

## 2. 原审计关键结论复核及纠错

| 原结论 | 本轮判定与证据 | 处置 |
|---|---|---|
| 1MB历史媒体是36秒延迟的主要原因，换引用即可明显提速 | **因果未证实**。36.3秒端到端中位数与9.6秒模型中位数样本/窗口不同，不能直接相减；模型、合并、Freshness、调度、换页均未控制 | 不改已提交媒体；拒绝按年龄将历史图替换为文字。逐段关键路径仍缺充分样本 |
| 正常聊天缓存异常 | **不能泛化**。最新24小时小双白当前模型前台74.56%，大肥鱼96.61%；辅助判断明显不同 | 保持表现良好的前台模型和 wire |
| 辅助零缓存与客户端无关，换模型是唯一办法 | **过度归因**。模型名称与缓存表现有关联，但端点身份、调用材料、时间等并非受控对照；实际User公共前缀少量样本也会显著变化 | 不全机器人换模型；保留已有稳定参考区和来源ID精简。辅助模型切换仍需质量/JSON/延迟对照 |
| 7次摘要全部因60秒超时失败 | **不成立**。留存7次尝试：1次提交成功，2次Provider成功但后处理ValueError，4次约60秒取消/超时；旧ValueError缺抛出位置 | 分别处理材料膨胀、读取预算和失败定位；不能把Coverage问题归入超时 |
| 摘要主要被图片base64撑大 | **该样本不成立**。small_summary wire 图片数、mediaChars均为0；视觉观察是单独请求 | 不粗暴截断工具/图片；消除重复Hold材料及同事件正文 |
| 16条blocked_unknown证明Presence都看到后决定沉默 | **不成立**。旧快照仅3条具备自身waiting/group_waiting、零副作用证据；其余不能猜归属。发布前自然增长为49条，启动后精确恢复6条，43条保留 | 修正新状态转换；旧数据只按真实账本恢复 |
| JSON失败由2048额度被隐藏推理耗尽 | **证据不足**。唯一明确JSONDecodeError对应stop、6输出Token、reasoningChars=0；没有length证据，也无原始completion | 不盲目增加预算/重试，不删模型思考 |
| Worker SIGKILL证明部署直接强杀 | **单凭时间重合不足**。现有ProcessHost已有受控stop、空闲等待及生命周期记录；退出信号还可能涉及退出超时升级 | 本轮沿既有受控机制；未将所有旧SIGKILL定性为手工直杀 |
| controller日志无界 | **已证实**：唯一写点直接append，生产84,172,266字节 | 已修复及部署轮转 |
| 磁盘可直接清理约10GB | **清单不可靠**：需区分活跃app、恢复材料及真正旧快照 | 本轮不照单删除生产/opt、数据库或受保护证据 |
| Swap导致首条消息慢 | **合理推断，贡献未知**。采样主机1775MiB、Swap已用1080MiB，PSI非零；没有逐请求缺页归因 | 不因猜测改内存软限/禁用回收 |
| 奶蛙静默、大肥鱼失效群是程序缺陷 | **业务配置与运行故障需区分** | 不擅自修改白名单、人格、主人关系或运营开关 |

原报告的“20:22部署”统计常量与其声称的22:22最终Worker换载相差两小时，不能用同一切分点证明代码效果。部分缓存脚本以全部输入作分母、只累加已知cached，实际上把未知当零；本报告按已报告缓存的输入分母计算。

## 3. 缓存与延迟：实际已知边界

只读采样截至11-10 00:51，向前24小时，限定completed请求；正/零/未知分别统计。命中率为已报告缓存行的Σcached/Σinput。

| 机器人/模型 | phase | 次数 | 正/零/未知 | 已知缓存加权率 |
|---|---|---:|---|---:|
| 小双白/deepseek-v4.1-flash | foreground |148|121/26/1|74.56%|
| 同上 | group_presence |43|5/36/2|0.78%|
| 同上 | private_freshness |39|2/36/1|6.68%|
| 同上 | owner_contact |21|1/19/1|0.13%|
| 同上 | group_freshness |7|0/7/0|0%|
| 大肥鱼/deepseek-flash | foreground |156|155/1/0|96.61%|
| 同上 | private_freshness |62|61/1/0|71.31%|
| 同上 | owner_contact |3|3/0/0|48.91%|
| 梁沅芷/gemini-3.8-flash | foreground |30|0/0/30|未知|
| 同上 | group_presence |9|0/0/9|未知|
| 同上 | owner_contact |15|0/0/15|未知|

`routingSha=e3b0…`是空prompt_cache_key哈希，**不是端点哈希**。不能据此宣布相同API端点已验证。少量实际User前缀测量：小双白Presence有低至81字符、Owner Contact低至87字符的样本，也有数千字符公共前缀；大肥鱼Private Freshness测量子集约18059～18061字符。字段覆盖有限，未保留正文，无法准确归因每次变动的具体资料区。

因此本轮未引入辅助Journal、未冻结动态事实、未更换主模型，也没有声称缓存率已提高。辅助专用模型对照是待验证选项，不是已部署成果。历史图片每次出现在wire与高缓存命中可以同时成立；没有证明图片贡献多少秒，就不编造收益。

## 4. 实际代码修改与最终行为

### 4.1 Work、群聊等待与可靠结算

路径：`behavior.preprocess → GroupPresence.evaluate → pipeline.ignored_inputs → finish/release_work`，延迟入口仍走既有Scope永久Work。

- 新增输入状态`deferred`，表示无外部副作用的群聊准入等待；由既有群收件箱/后续真实事件接管，不进入即时无限重放。
- `claim_scope/join_active`接管该状态；执行所有权仍由`pipeline_scope_work`和`status=owned`决定。释放后保留事件owner作审计来源，不代表继续持锁。
- deferred_group属于真实消息处理，恢复正确账本所有权，能正常续处理；unknown、partial、已有副作用、已settled/held的输入不允许借延迟入口重放。
- Presence的“不参与”只对本次有界视图中完整呈现的可信纯文本原始事件结算；原始payload与投影正文逐字核对。@、引用、媒体、截断信息不被片段误结算。
- policy拒绝、超时、陈旧结果不是模型已经理解全部输入的证据。
- 每次激活外层4次限制保持；SDK工具续轮不按一次工具计一次外层。内部生产5步保持，未发现当前业务需要放宽的明确失败链；12次工具交互测试使用明确的fixture预算16，不宣称生产支持12次。

群聊窗口不是待结算输入总数：Presence默认12条，可配3～30条，每条300字/总3000字；Ambient还有自身截断，SDK配置历史100条。持续沉默不会停止接收或把累计积压当成单次模型视图。

### 4.2 摘要材料、超时与恢复

路径：`Reflection.retry → turns/observed_requests → summary_material.enrich/model_view → Provider → 原有事务/coverage/checkpoint`。

- 正常35轮批次保留选中的既有Hold材料；额外pending补充不再把其他旧Hold整表反复送入模型。它们仍保留在原权威材料、Journal和既有恢复流程，没有删除、伪造覆盖或关闭恢复。
- `model_view`仅在event+sender唯一匹配且正文完全相等时，省略observed_requests中已在turns提供的text/confirmed_reply。不同作者、同文不同事件、不同媒体/回执内容不合并。
- 保留native输出归属、input_settlement、工具参数/结果、图像观察与来源。工具返回不做全局截断。
- 原始材料和Coverage digest不变；大/小摘要类型、第三小摘要连续大融合、35有效业务轮计数及许可消费机制均不改。
- 同一生产隔离快照装配：旧389个turns/387份材料，排除未选旧Hold后124个turns/122份材料；组合JSON约346451→123118字符。此为**离线装配字符对比**，不是生产Token/缓存/质量提升比例。另发现旧重复正文约6.3万字符；与前一差值存在重叠，不能相加当收益。
- summary/big_merge单请求read=180秒，外层185秒；connect/pool/write不放大，不改变共享client，不增加SDK自动重试。summary_image原90秒、foreground/Presence/Freshness保持原预算。
- 后处理异常记录类名和`module/line/symbol`，不写异常正文、聊天内容或凭据，保留原持久化退避。

旧273条Hold不是本轮“已全部解决”：当前小双白298条（290 NULL-round），检查点#5/#7 available、#6 consumed；这些状态在本轮发布前备份已存在，不能归功于新修复。发布后尚无小双白新摘要请求，必须等待自然周期验证实际成功与退休范围。

### 4.3 日志及存储治理

`scripts/linux-memory-controller.py`唯一controller.jsonl写点改为完整行写入前轮转，50MiB、两代。旧84MB文件完整转为`.1`，本轮没有立即删除旧证据。运行中的独立guard继续active；controller受控重启后状态更新正常。

不改变48/72/24MiB软目标，不设置硬限制，不重启QQ。原生产脚本与Git旧源码差异经核实仅CRLF/LF；先验证文本等价，再按实际文件哈希CAS，不强行覆盖未知逻辑差异。

## 5. 测试与失败处理

实际命令及最终结果（均无付费Provider、无真实QQ外发测试）：

| 命令 | 结果及证明范围 |
|---|---|
| `python tests/work_hold_contract_20261109_test.py` |17 PASS；100条接管、deferred、unknown禁止重放、部分退休与前缀保护|
| `python -m unittest discover -s tests -p autonomy_test.py -q` |57 PASS；窗口、完整原文/截断/媒体、超时不结算|
| `python tests/summary_material_test.py` |20 PASS；去重、作者边界、旧Hold保护、checkpoint|
| `python tests/summary_transport_budget_20261110_test.py` |1 PASS；仅摘要读取预算改变|
| `python tests/null_hold_recovery_20261109_test.py` |7 PASS；原批次恢复、无新许可|
| `python tests/summary_checkpoint_foundation_test.py` |7 PASS|
| `python tests/context_foundation_20261109_test.py` |5 PASS|
| `python tests/permanent_work_contract_20261109_test.py` |10 PASS；永久ID、未回应输入再接管及4次限制|
| `python tests/readonly_journal_test.py` |27 PASS|
| `python tests/journal_storage_test.py` |8 PASS|
| `python tests/sdk_tool_continuations_20261109_test.py` |2 PASS；真实SDK+Mock，明确fixture16步完成12工具交互|
| `python tests/linux_memory_controller_test.py` |20 PASS；含轮转完整行、有限代及既有资源所有权|
| `node --import tsx --test --test-concurrency=1 tests/scope-work-runtime.test.ts tests/bot-pipeline.test.ts tests/process-host.test.ts` |6 PASS；真实SDK+Mock模型/协议，连续2/10/100输入、多人物、keep/replace/drop、4次交接、前缀及退出信号|
| `npm run build:production` |PASS；既有前端chunk大小warning，非失败|
| `npm run check:production` |PASS；编译产物、API、数据库/登录持久化与重启的本地smoke|
| `git diff --check` |PASS|

初跑失败没有隐藏：两个旧断言分别要求pending owner清空、deferred不属于真实输入；依据新职责修正，并增加“没有Scope锁、后续可以接管、owner正确更新”的断言。真实SDK集成初跑还暴露Mock HTTP分块解码中文变成`�`，造成工具Schema假抖动；加入`req.setEncoding('utf8')`，保留完整Schema相等断言后复跑通过。不是放宽工具稳定要求。

本轮未重复执行全仓昂贵矩阵。没有真实Provider A/B、没有旧图片视觉降质实验（未改图）、没有主动发送QQ测试消息。未执行项不能记作PASS。

## 6. 发布、版本及真实证据

Git：初始已有整改先提交推送`6330fe663fb72e960953873152fcf9ab1ffa4dac`；本轮业务修复`0e3b223c5181bfd2e00d3259012debeaac375941`；控制器`f4ea015e7d0cd3debe399770f6ce470b474c1c85`。均推送既有`origin/main`。原有未跟踪旧报告未混入提交。

- CAS比对67个Python源文件，候选仅本轮7文件，未解释生产差异0。安装后67文件无hash不匹配。
- AI备份：`/opt/fishbot-pg-cutover-20261031/control-final-c/audit-converge-deploy-20261110/release-backup-20261109T171356Z`，含变更代码、PG dump、17份一致SQLite备份；Journal另做消息哈希证明。
- 发布manifest SHA：`64ee2ef6a1d100e486fa9bae02238b2df0f777fa1997fa8e5ec2ec725f817343`。其dirtyWorktree标记为预检脚本固定元数据，源码实际对应上述已提交文件；不能仅依赖该标签判断运行版本。
- 两组AI旧PID2790854/2790991→新PID2825235/2825479，11-10 01:14:31、01:15:35完成。ProcessHost空闲边界受控stop/start，Node Agent PID2556741不变。
- 四个协议PID2556847、2557026、2557060、2557257及创建时间不变；4个QQ online、AI ready，自主协调器恢复运行。
- 两组实际加载attestation各15项函数`sourceMatchesLoaded=true`，不是只看磁盘；本轮未改Node业务，dist-server旧buildCommit仍为旧Node构建，不能误称整包已重建上线。
- controller SHA：`0cd57f4bb244fff24d488035103829fbf87a6d4442ee6d74f90d76976e32dccd`；备份`audit-converge-deploy-20261110/controller-before.py`。guard未重启，controller/guard均active，stateAge约0.53秒。
- 最终Journal验证：5个记录上下文，1554条原消息前缀及System一致；无数据库结构迁移、无历史重建、无旧许可复活。

自然流量：梁沅芷新Worker PID2825479已出现foreground完成、private_freshness完成，以及两项sent账本合计attempted=confirmed=7。另有一次68.8秒`APITimeoutError`，随后正常请求成功；没有证据证明其具体网络/上游根因，不增加盲目重试。Gemini缓存字段未报告，不能计零。该样本证明部分真实聊天恢复工作，不代表其它机器人新功能或长期稳定全部验收。

目标群发布前49条blocked_unknown变为43条blocked_unknown+6条deferred，来源是精确零副作用waiting账本自动恢复；没有机械ignored、没有发消息验证，尚无该群新自然Presence样本。

回滚：受控空闲边界恢复备份代码并重新换载对应AI；controller恢复controller-before.py并受控重启。**不得将旧SQLite/PG备份覆盖上线后新增历史、回执、业务轮或许可。** 本轮deferred为持久化状态，若回滚到不认识该状态的旧逻辑，必须先提供只读/等待处理支持，不能批量伪造结算或重放unknown。

## 7. 工作区清理与全Codex搜索规则

清理清单及结果在`.local/cleanup-inventory-20261110/`：删除5069个目录、174761428681逻辑字节，失败0；D盘free67628789760→243155050496字节。此数不是生产磁盘清理量。

**清理缺陷披露**：基于测试mkdtemp前缀的匹配过宽，误选约509582字节的`scope-work-final-20261109`旧发布辅助目录，不能把全部5069都表述为纯测试沙箱。已从服务器旧stage取回18份发布辅助文件，并恢复manifest/hash索引；旧本地独有观察脚本/测试日志没有全部恢复，明确记录于该目录`RECOVERY-NOTE.json`。生产备份、源报告、生产历史和本轮证据仍在。清单生成已收紧到随机后缀匹配，命名发布目录保留。后续清理必须检查内部职责，不能只按目录前缀猜测。

全Codex用户级AGENTS及hooks.json已配置任务开始/用户输入/子智能体和rg执行前的50%磁盘活动率软目标；当前会话已收到对应全局hook上下文，证明提示注入生效。它是准入与行为护栏，不是操作系统硬限速，也不保证任何时刻磁盘活动率≤50%。

## 8. Plan vs Actual、剩余风险

| 原任务 | 实际结果/偏移 | 状态 |
|---|---|---|
| 先推Git、保护并行成果 | 初始及本轮分提交已推；未添加既有未跟踪私有报告 |完成|
| 群输入结算缺陷 | 修复新链路，生产精确恢复6条；43条缺证旧输入保留，不伪造ignored |已部署；部分旧数据未解决|
| 35轮摘要停摆 | 材料去重、旧Hold有界恢复、专用超时、失败定位已实现与测试；新自然摘要尚未出现 |已部署，真实周期待验|
| 历史媒体延迟 | 推翻直接年龄替换方案；既有wire完全保留，额外延迟贡献未知 |保持正确机制，性能归因未完成|
| 辅助缓存与模型 | 纠正统计/因果；未贸然切换当前好用模型，无新收益可宣称 |已复核，辅助路由对照暂缓|
| JSON/工具预算 | 明确JSON根因未知；外层4/内部5保持，12工具fixture明确 |保留现状，有证据再改|
| 日志增长 | controller轮转本地20测试及生产实际轮转通过 |完成并部署|
| 运行版本与历史保护 | 实际加载、67源hash、协议所有权、1554消息/System验证 |完成；长期运行仍待观察|
| 生产磁盘/数据库债 | 未删除不明/活跃数据，无证据不批量清库 |暂缓；生产磁盘84%仍需独立精确清单|
| 本地空间治理 | 大量回收完成；误删辅助目录已披露并部分恢复 |完成空间治理，旧本地证据损失未完全恢复|

主要后续观察：小双白下一次真实小摘要/第三小融合及合法退休；NULL-round旧材料能恢复多少；新群Presence是否持续滚动而不误结算；辅助调用按相同模型/phase的自然usage；APITimeoutError是否重复。没有取得样本时不能宣布通过。

本轮没有恢复外层规划AI，没有新增事实权威、摘要类型或辅助历史，没有改人格、主人important、@可NO_REPLY、约定权限及未知副作用保护。真正实施的性能优化限于已证明重复的模型材料和有界日志；未编造Token、缓存、延迟或回复质量改善百分比。
