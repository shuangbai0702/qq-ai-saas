# FishBot R4 前台整改实施与离线验收

状态：已实施并验证的待部署候选；不等于生产验收通过。用户后续明确暂缓外层自主规划，本候选相对 Linux 基线不修改 autonomous_runtime.py、decision_history.py 或外层周期。

## 版本与范围

- Linux 源码基线提交：`4f22bd5`。通过 SSH 只读导出 `/opt/fishbot-pg-cutover-20261031/app-final-b` 的现有源码；导出包含247个现存文件，提交仅保存运行源码，不用部署包里的旧测试覆盖现有测试。逐文件证据见 `FishBot-linux-source-baseline-20261107.json`。
- 在独立 Git worktree 和分支制作候选，原始脏工作区保留。没有把整个脏工作区提交。
- 服务器导出和 Linux 内存测试没有文件写入。没有部署、重启、配置变更、数据库写入、模型请求或真实 QQ 发送。
- 不使用 developer 角色；没有新增权限或长期记忆系统。凭据文件、抓包正文、运行数据库均不进入提交。

## Plan vs Actual

| 要求 | 实际结果 | 状态 |
|---|---|---|
| 先冻结 Linux 基线 | 独立基线提交、源码哈希归档 | 完成 |
| 前台 System 稳定 | 全部 System 向量在最终 HTTP 边界检查；身份和能力作为平台当前事件数据，后端继续授权；空艾特不另换 System | 已实施、选定测试通过 |
| 35轮重构许可 | 仅成功处理精确35条完整业务记录的摘要事务签发一次性许可；失败、30轮摘要、大摘要合并不签发 | 已实施、单元及SQLite测试通过 |
| 用户网页保存 | 认证配置保存事务写 actor/revision/source 审计；节点查询后通过可信IPC传递许可 | 已实施、API隔离测试通过 |
| 前缀只追加 | strict journal + 最终HTTP精确序列化前缀检查；失败/取消撤销候选，过期候选不覆盖新基线 | 已实施、集成测试通过 |
| 摘要并发 | 比较可见摘要、覆盖、epoch和同会话摘要水位，阻止await期间提交的摘要被旧候选跨越 | 已实施、确定性barrier测试通过 |
| 重启恢复 | 私有runtime SQLite保存真实wire快照、版本及哈希，有界；群轮次证明恢复为tuple | 已实施、内存SQLite重建测试通过；进程级恢复未验收 |
| 上下文精简 | 相同policy只承载一次；后续平台事件数据包省略相同policy，仍保存完整当前authority用于后续比较 | 已实施、语义还原及合成35轮验证通过 |
| 完整真实35轮体积/质量/成本 | 现有17条前台抓包不足以证明完整连续35轮；未发新模型请求 | 未完成，生产发布门槛 |
| 输出补丁 | 去掉实时英文启发式吞正文；只处理独立reasoning字段和明确控制标记 | 已实施、真实发送包装路径夹具通过 |
| 完整Linux、多租户长时间压力 | Linux执行19项纯内存journal测试；未运行完整SDK发送栈及长期多租户压力 | 部分完成 |
| 生产上线 | 用户未授权，本轮未执行 | 不在范围 |

## 改动与状态转换

`readonly_journal.py:ReadonlyJournal.prepare/commit/abort_candidate/retire_committed`：保留已成功提交前缀；变更的当前事件与只读状态以尾部增量承载；未获许可的native差异、摘要依赖变化、容量冲突不再静默重建。状态区分候选、已提交、已退役、恢复失败。候选serial隔离迟到提交。持久化容量按UTF-8字节计算，单机器人快照总预算16MiB、最多64个ready journal，并保留有界失效标记，拒绝用LRU重新bootstrap绕过护栏。

`system_contract.py:SystemContract.reserve/settle/system_vector_sha` 与 `model_cache.py:observe_wire_request`：检查所有System消息的数量、位置、角色、字段顺序和内容的序列化哈希。合同不按Work/phase/scope重新创建。最终HTTP消息必须具有journal候选的完整精确前缀，允许末尾追加；违规在发送前抛出明确错误，诊断无正文。System变更目前仅接入可信网页保存许可；成功35轮摘要不强制改变System，因此保持稳定System同样合法。

`memory_reflection.py:retry/reserve_context_reconstruction/settle_context_reconstruction/context_dependency`：摘要成功事务绑定scope、through_round_id、epoch、summary_id及35轮证明；一次性预留、成功消费、失败释放。摘要行ID/更新时间自身不视为业务变化，正文、来源、覆盖、遗忘epoch以及同会话摘要检查点水位仍参与检查。

`behavior.py:inject/prepare_chat_payload/settle_journal/send`：前台启用strict与policy增量；冻结System基础人格和协议说明。动态身份、事件事实和能力说明在现有平台资料包中呈现，工具集仍依法实时变化，真实授权继续由后端处理。取消、500和旧配置结果不能清空成功前缀。空艾特的事件说明移入真实事件资料，保留配置人格。所有OutputPlan消息确认后才计入完整业务轮；已确认图片/语音等纯媒体输出记录其真实类型，不编造内容。未知回执、部分发送不计完整轮次。

`context_protocol.py/context_transport.py`：context_references协议说明固定，不再因本轮引用形式切换System。policy省略语义由平台数据包说明，完整历史对象不被重编码。没有缩短35轮、13条群消息或删除人物事实/工具回执。

`output_clean.py`：without_analysis兼容函数不按英文比例、Planning标题等推断删除。prepare_history不加入这类过滤。`behavior.py:suppress_separate_reasoning`只在提供独立reasoning、无content且无tool calls时清空误映射可见文本；无标记content内的分析仍无法可靠识别。

`app.ts/agent.ts/RuntimeContext/shared.ts/index.ts/shared-worker.py/headless.py`：认证网页配置保存的审计许可传输；未采信请求体自报的身份或许可来源。

## 实际验证

- Windows候选worktree：14组相关Python模块共205项通过，包含journal、System合同、wire钩子、摘要、事实、跨群私记忆、来源、核心便签、容量等。
- Windows候选worktree：Node/API隔离测试12项通过，包含认证、租户隔离、配置CAS、可信审计及Python行为入口。
- Windows候选worktree：`tests/send_lifecycle_test.py`通过实际安装后的发送包装器；覆盖正常中英文、`**Planning**`正文、精确NO_REPLY、真正空文本、独立reasoning、图片、语音、带NO_REPLY文本的图片、确认回执、部分超时和晚到结果。仅使用替身协议，无网络发送。
- Windows候选worktree：`tests/foreground_prompt_test.py`通过真实provider注入和空艾特入口；验证System不随事件参与规则变化，事件资料保留，遗忘后旧请求被阻止。
- TypeScript `tsc --noEmit`通过。
- Linux：将候选模块通过stdin加载至独立Python进程内存，19项journal测试通过；只使用`:memory:` SQLite，不写服务器文件、不加载生产机器人、不触发模型或QQ。
- 原脏工作区广泛回归曾出现4个容量相关错误，其中3个来自暂缓的本地外层候选，1个是前台测试丢掉已提交native历史。候选分支保留Linux外层原样，并修正前台夹具为严格追加；上述205项在候选分支全部通过。没有删除或跳过这些测试。

## 精简实验与证据边界

详细逐轮数据：`FishBot-R4-foreground-benchmark-20261107.json`。使用一份真实抓包中5958字符的固定策略字节，搭建35轮隔离传输轨迹；业务输入及状态是合成替身，不是完整真实35轮。

| 指标 | 每轮重复完整policy | 相同policy只承载一次 |
|---|---:|---:|
| 首请求体积 | 6881字符 | 6881字符 |
| 第35轮累计wire | 241970字符 | 22194字符 |
| 35轮准备/提交总耗时（单次本机测量） | 150.30ms | 131.78ms |
| Python tracemalloc峰值 | 772420字节 | 306628字节 |

仅此重复载荷实验减少219776字符（90.83%），34次续接均保留完整旧前缀，最新只读状态逐轮精确还原。不能据此宣称真实流量减少90.83%、实际Token/账单下降同样比例或上游缓存一定命中；没有估算计费Token。

## 未完成项、冲突与生产门槛

1. 缺少可证明完整连续35轮的真实前台离线轨迹及质量评分，尚未证明大量工具结果/媒体情况下不会在35轮前容量阻塞。384K/增量/媒体保护仍保留，越界明确拒绝，不提高上限掩盖问题。
2. 旧生产会话没有新格式wire持久化快照时会拒绝无许可bootstrap。需要先离线证明兼容恢复或在合法用户保存/35轮边界完成迁移；不能直接全量上线制造停止回复。
3. 遗忘在35轮前发生时，“旧前缀绝对不变”和“立即不再发送已遗忘信息”可能不可同时满足。当前选择明确退役并阻止继续发送旧资料，未增设提前重构例外；恢复需要合法许可。这是可用性冲突，尚未完成产品与运行验收。
4. 旧配置使用非35摘要周期、关闭记忆/摘要时，不会获得35轮摘要许可。需在离线验收中明确其正常生命周期，不能假定配置升级本身就是网页许可。
5. wire持久化、摘要许可消费与进程异常退出仍需进程级故障注入；纯SQLite重建测试不等于崩溃恢复全部通过。存储故障坚持失败关闭，尚未证明可用性。
6. 完整Linux SDK发送路径、共享多租户长期压力、实际上游费用/延迟/质量对照未完成。上游满前缀返回0原因仍不可观测；没有宣称修复。
7. 无标记分析文本出现在content中的剩余风险保留，不用启发式吞掉正常英文回复。

以上项目是发布阻断项。候选可供代码审查与下一轮离线验收，不能标记“全部R4完成/可直接上线”。

## 发布与回退材料

候选文件精确哈希见 `FishBot-R4-foreground-candidate-sha256-20261107.json`。先审查相对`4f22bd5`的候选差异；禁止直接发布原始脏工作区。实际部署需另获明确授权，并先满足上述门槛，再冻结线上版本、离线备份私有runtime、进行无外部副作用恢复验证。

代码回退以Linux基线`4f22bd5`为对照，不能删除新增私有journal/许可表来重获bootstrap；不能通过恢复旧数据库复活已遗忘资料或已消费许可。保留外部操作回执，未知操作不自动重试。需要数据库回退时必须有独立迁移/遗忘一致性证明，本轮没有执行数据库迁移或回退。


## 2026-11-07 补充实施：用户授权的一次性新起点

用户最新明确要求：无法恢复的旧历史不恢复，保证新版本开始后的正确性。依此新增一个公开、受限的一次性迁移路径；它不是35轮摘要许可、不是网页保存许可，也不是以后重启可重复使用的例外。旧的事实、摘要、来源、约定、回执与执行记录不删除。

- `journal_cutover.enroll_legacy_scopes(db, release)`：只供发布工具在持有对应worker且已暂停入站时调用。仅为当前机器人私有数据库中已存在的合法群/私scope登记一次性迁移行，记录40位源码提交号；排除self摘要等非前台scope，4096行上限。已有guarded journal表时拒绝补发，已登记时幂等且不能换release重发。
- `ReadonlyJournal._load/_persist`：只有显式登记、尚未消费且没有快照的scope可建立新基线。首次成功快照写入与消费同一SQLite事务；取消不消费。后续删除/丢失快照仍失败关闭，不能利用重启、表中旧摘要或旧配置再次bootstrap。线上尚未执行登记。
- `confirmed_native_window_extension`：SDK只提供30轮窗口时，以带合法事件来源的逐消息完整尾/头相等作为续接证据。wire完整保留旧历史，仅追加新尾部，不把native窗口裁剪视为重构。35轮夹具验证35条问题全部留在wire，System只有原来一条；无来源相同正文不能证明续接。
- `Reflection.context_dependency`：修正循环变量scope遮蔽请求scope的问题；来自其他会话的摘要不能把当前请求的检查点水位改为其所属会话。新增回归同时证明无scope调用不受到该水位影响。
- `behavior.prepare_chat_payload`：明确foreground才启用新增重构许可护栏；被用户排除的内部自主Work不顺带取得此前台护栏。没有修改外层autonomous_runtime.py、decision_history.py。
- `installForegroundContextRuntime`：扩展已有有界维护入口，仅更新可信配置context、applyConfig和shared.attach三个现有方法，保持Agent、协议ChildProcess、stdout管道、自主规划/收藏/B站owner原对象；失败恢复方法引用。真实隔离子进程测试证明对象/PID不变。尚未在线调用。

### 最新验证与Plan vs Actual

| 要求 | 实际证据 | 状态 |
|---|---|---|
| 旧记录无需恢复，建立新起点 | 一次性登记/消费/取消/重启/删除快照负例 | 本地完成，未生产迁移 |
| 35轮前严格追加 | 30轮SDK窗口下35轮精确旧前缀断言，原合同测试 | 已完成隔离验证 |
| 外层暂不整改 | 相对Linux基线两外层源码无差异；内部Work不启用前台guard测试 | 已落实本轮边界 |
| 前台摘要依赖正确 | 跨scope水位负例及既有摘要并发测试 | 已完成 |
| Windows/Linux行为回归 | 两端各211项Python通过；Windows Node/API/维护13项通过；tsc通过 | 已执行 |
| 真实发送包装器 | Windows/Linux独立脚本使用替身协议验证，无真实QQ/模型请求 | 见对应实际日志，不等同线上验收 |
| 不重启QQ/Agent | 维护函数隔离真实子进程测试；线上未调用任何restart/stop | 已保持 |
| 正常35轮真实轨迹容量 | 当前抓包仅17个前台请求；现存单scope完整轮次最多34，摘要已覆盖旧原文 | 未完成，不能以合成35轮冒充 |
| 生产应用 | 只在control私有目录放隔离测试源码，生产挂载源码/配置/数据库未替换 | 未执行 |

Linux首轮隔离运行因sandbox遗漏scripts/runtime_memory.py和templates/model-media.json有2个错误；补齐隔离目录到既有只读资源的链接后，未改断言、未跳用例，完整211项复验通过。阶段0–5的新增小范围工作已验证；真实35轮容量、崩溃故障注入、多租户长期压力及线上验收仍未收口。因此此次用户授权解决了“旧历史必须恢复”的阻塞，**没有把其解释为豁免正常容量与可用性验收**。

发布门槛继续遵循用户原文：在隔离环境证明正常35轮真实轨迹不会因容量问题频繁阻塞之前，不发布对应护栏到生产。缺少的轨迹不能用字符除以3、合成轮次或数据库仍有facts替代。当前待部署，不能宣称已在线生效。生产账号状态以只读观测为准；未主动发起QQ或模型测试请求。
