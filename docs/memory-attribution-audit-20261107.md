# fishbot 记忆归属类问题系统审计报告

- 日期：2026-11-07
- 审计范围：`qq-ai-saas`（adapters/astrbot、apps/node-agent、packages/*）+ 生产环境 110.41.11.66（fishbot-production 全套容器）
- 触发病例：2026-11-07 00:15（北京时间），bot「我的大肥鱼/小双白」(7356b371) 在私聊中对用户双白(768687099)说"你刚说亏了几万、又问这是啥模型"——而这两句话是用户当时在群里说的。
- 审计方法：三路并行代码审计（执行链 / 渲染链 / 写入链）+ 生产数据取证（runtime.log、model-diagnostics.jsonl、model-request-captures、behavior.sqlite）交叉验证。
- 结论一句话：**这不是一个 bug，是一类病**。生成端、检索端、写入端各有一组缺口，共同模式是"出处信息在某次传递中被丢掉，之后模型在错误前提下脑补"。已确认 4 起生产病例、9 处结构性断点、5 条自毒通道。系统骨架（facts 隔离、provenance 不可重标、handoff 出处）是好的，缺口集中，可修。

---

## 1. 病例完整还原（证据链）

时间说明：服务器 protocol 日志为 UTC，QQ 客户端显示为北京时间（UTC+8）。下表同时给两种时间。

| UTC | 北京 | 事件 | 证据 |
|---|---|---|---|
| 11-06 10:45 | 18:45 | 私聊：用户喊"小鲸鱼！"，bot 回"怎么突然喊我小鲸鱼"；18:46 用户"想我了没"，bot 回"你刚才**连着**喊我小鲸鱼、问我在做什么"（用户实际只喊了一声——时间/次数放大，同类轻症） | runtime.log 行 583/586/602；memory_round_material rowid=122 |
| 11-06 16:07 | 00:07 | 原神群(1103513828)：用户发"卧槽了""被腾讯搞了""**亏了几万**" | behavior.sqlite social_samples id=8413/8414/8415（scope=group:1103513828, subject=768687099） |
| 11-06 16:12 | 00:12 | 同一群：用户问"**这是啥模型**"，群友回"移动的agent" | social_samples id=8417；pipeline_quote_sources handle=qr_631ca3594c77f7e3a4be |
| 11-06 16:12~16:15 | 00:12~00:15 | 这些群消息在准入层全部被拒：`stage=admission, status=silent, reason=group_participation_not_allowed`（该群不在 groups 配置） | pipeline_events rowid=6883~6889 |
| 11-06 16:15:17 | 00:15:17 | 外层自治决策（cycleId ff71c7c4）决定主动私聊。planner 写的任务目标原文：**"私聊接住主人刚在群里提到的'亏了几万'和'这是啥模型'，询问他具体是在说哪件事、希望我帮他分析模型还是损失情况；先确认事实，不猜测原因，也不转述其他群友内容。"** —— planner 知道真相 | pipeline_actions rowid=297 `task.objective` |
| 11-06 16:15:24 | 00:15:24 | 执行器生成消息：**"你刚说亏了几万、又问这是啥模型，我有点担心……"**——"在群里"这个归属没了。该请求 23,245 tokens、modelCalls=1，可见上下文 = task JSON + 私聊会话历史（私聊历史里没有这两句话） | pipeline_actions rowid=297 `response`；effects[].method=send_private_msg |
| 11-06 16:22 | 00:22 | 用户质问"我啥时候说得"。执行器可见上下文（私聊历史）只有 bot 自己那句错误归属的话，群上下文不在场；模型翻长程记忆找补，抓到 10:45 的"小鲸鱼"和某次"你为啥一直思考，不行动"，输出半道歉半脑补的混合叙述 | memory_rounds rowid=989；memory_round_material rowid=138 |

**根因定性**：planner→executor 的任务传递中，引用内容的来源 scope 只存在于 objective 的散文里，没有结构化字段承载；执行器 prompt 无任何"引用其他会话必须保留归属"的规则。用户视角下"你刚说"=「你在私聊里对我说过」，前提已错，后续解释全是脑补。

---

## 2. 病类 A：跨 scope 归属丢失（生成端，病例根因链）

同一模式"planner 知道、executor 不知道"共 9 处断点，按数据流顺序：

### A1. 合同层：action 无出处字段（根因）
- 位置：`packages/contracts/autonomous.ts:36-42`（ActionIntentSchema）
- 问题：`action = z.object({goalId,kind,target,objective}).strict()`。goalChanges 有可选 `sourceEventId`（:34），但 `action`（kind=message）没有任何出处字段；`.strict()` 使模型**即使想填 sourceScope 也不允许**。
- 同类：`adapters/astrbot/autonomous_runtime.py:579` 的回退 schema 同样只有 `{goalId,kind,target,objective}`。

### A2. 数据库层：自发目标 source={}
- 位置：`packages/database/autonomous.ts:196-199`（commitDecision）
- 问题：`delegated=Boolean(change.sourceEventId)`；自发目标（即使 objective 正文写着"主人刚在群里提到的"）落库为 `source={}`，出处永久为空。"对话衍生 vs 模型自发"的判定完全交给模型，跨会话衍生目标（群 observations → 私聊目标）处于灰区，必然落空。
- 对照（正确实现）：handoff 建目标路径 `database/autonomous.ts:104-105` 的 source 带 eventId/scope/senderId/owner。

### A3. 派发层：跨会话目标拿不到 requestSource
- 位置：`apps/node-agent/src/autonomous.ts:211`
- 问题：`requestSource` 仅在 `source.scope===action.target` 时附加。群来源 → 私聊目标时只剩裸 `sourceScope`（还来自 A2 的空 source，实际是 null）。注释明言 "Source comes from the durable accepted goal, never the model action" —— action 被设计为无出处载体。

### A4. 执行入口：objective 变成目标会话内的合成消息
- 位置：`adapters/astrbot/autonomous_runtime.py:733-746`（make_event）
- 问题：`msg.sender=MessageMember(user_id='0',nickname='自主服务任务'); msg.message=[Plain(objective)]`，`msg.type` 按 **target** scope 设定。objective 里引用的他人发言被放进收件会话的语境中，无任何"原文来自 group:xxx"标注。
- 关联：`bot_pipeline.py:281-295`（bind_internal）的 Work 结构 `{bot_id,scope,'service:'+botId,self_id,identity['id'],'autonomous',...}` 没有上游出处字段——整个内层管线结构上无法表达"这段任务文本源自另一个会话"。

### A5. 执行器 prompt：sourceScope 无语义、无归属规则（病例最后一公里）
- 位置：`adapters/astrbot/autonomous_runtime.py:810`（prepare_context 按 target scope 装配）、`:820`（`prompt=json.dumps({'task':action,'priorFeedback':prior})`）、`:821-824`（system prompt）
- 问题：system 只解释 "sender=0是服务身份…真实联系人是recipient"，**全链路无一处解释 task.sourceScope 的语义**，也没有"引用其他会话内容须保留归属、不得改口'你刚说'"的指令。即使 A1-A3 修了、sourceScope 有值，执行器也不知道它是什么。`behavior.py:351-374`（internal_context）+ `context_transport.py:26-38` 还会把与 prompt 相同的 task 去重，执行器最终只看到 prompt 里那份无解释的 task。

### A6. 披露策略不管归属
- 位置：`adapters/astrbot/bot_pipeline.py:17`（EXECUTION_DISCLOSURE_POLICY）
- 问题：只管"内部实现保密 + 他人私聊保密"，不管理"在群里说的"→"你刚说"这类归属漂移。

### A7. 说话人标注不覆盖自治来源
- 位置：`bot_pipeline.py:151-162`（annotate_speaker）
- 问题：仅 `source in ('message','deferred_group')` 附加 `[QQ消息来源]`；autonomous/scheduled_contact/owner_contact 一律不加。自主任务的 objective 在 native 历史里无来源标注。

### A8. null 归属持久化放大
- 位置：`autonomous_runtime.py:888`（feedback.task.sourceScope=null 写入 pipeline_actions）→ `bot_pipeline.py:97-130`（experience_context `allowed()` :102-103 对无 sourceScope 的 message 项按 target 匹配放行；:116-117 持久化时 sourceScope 优先）
- 后果：无出处的执行经历进入后续规划周期，出处缺失代代相传。

### A9. 反向病例：initiative 合成事件把平台文案标成真实联系人
- 位置：`adapters/astrbot/initiative.py:201-206`
- 问题：主动触发的合成事件把**平台调度文案**放进 `sender=user_id:item['subject']`（真实联系人/群内最后发言者）、`nickname='已授权会话'` 的消息里；与 make_event 的 sender='0' 约定相反，且 annotate_speaker 不覆盖 scheduled_contact/owner_contact。回复模型可能把"（平台内部主动聊天时刻…）"当成对方说的话。
- 附带：`autonomous_runtime.py:813-818`（priorFeedback 双向缺口）——消息类 prior 整体丢弃（连同归属）；研究类 prior 携带 null sourceScope 材料进入下一执行器。

**做得好的（别动）**：decision_foreground（autonomous_runtime.py:109-118）、shared_context（:425-460，按 (source_scope,sender_id,source_event) 去重）、handoff 落库出处、decision_context/decision_wire 线格式层、group_freshness 样本——执行器上下文里**原料是够的**，断的是 objective ↔ 上下文出处之间的桥。

---

## 3. 病类 B：记忆检索渲染丢归属

### B1. 跨 scope 群摘要无人物归属（最高危）
- 位置：`adapters/astrbot/memory_scope.py:53-57`（summary_item）；闸门在 :43-49（summary_rows 经 `memory_summary_people` :48）
- 问题：条目只有 `source_scope`，**没有 subject/参与者字段**；people 集合只当准入闸门，**从不渲染进条目**。"群里谁说的"结构上无解，只能赌摘要正文写了。病例机制的后半段入口。

### B2. 摘要正文归属全靠软指令
- 位置：`adapters/astrbot/memory_reflection.py:446-447`（大摘要 system "保留来源会话和人物QQ归属"）、`companion_insights.py:10-11`
- 问题：纯靠指令，无校验；摘要里的"最近/刚才"凝固在摘要时刻，**之后永不重算**。

### B3. 预计算相对时间被删掉，留给模型脑补
- 位置：`adapters/astrbot/context_transport.py:51-55`（social_context_view.age() 只要存在绝对 `at` 就删掉 `age_seconds`）
- 问题：历史条目只剩 raw epoch 浮点，模型自行做 epoch→墙钟换算（模型对此很弱）。锚点只有当轮 system_reminder 的 Current datetime（:134,:235-239）。"你刚才**连着**喊我小鲸鱼"这类时间/次数放大由此而来。
- 对照（正确实现）：memory_scope.py:69（pending 渲染时现算 age_seconds）、decision_context.py:115,:204-206（age 每轮刷新）。

### B4. 其他
- `runtime_care.py:24-26`（Ambient.context）：ambient 群消息片段无时间、无事件 ID。
- `context_transport.py:157-183`（experience_view）：objective 截 100 字、reason 截 80-96 字，跨 scope 限定语可能正好被截掉；outerDecision.reason 整体剥离。
- `semantic_context.py:28`：摘要截 300 字，写在尾部的归属语可能被切。
- `context_transport.py:292`：缺 source_scope 的行会被隐式归到当前会话（当前链路都带，latent）。
- `context_transport.py:232`（_user_content）：历史信封剥 owner/owner_id（设计使然——身份不跨轮继承，但模型只能靠当轮身份+sender_id 对照判断"这句是不是主人说的"）。

---

## 4. 病类 C：记忆写入自毒闭环（一条幻觉如何变成"事实"）

### 主链（本病例已实际走过的路径）

```
[幻觉诞生] bot 回复断言未发生/归属错误的内容
   │
   ① 入素材：memory_rounds.answer / memory_round_material.reply
      behavior.py:1034 → memory_reflection.record
      summary_material.py:52-65 额外捕获未发送的中间模型输出(native_reply)
      （memory_reflection.py:256 remember(...,confirmed_reply(...)) 持久化）
   │
   ② 摘要轮：memory_reflection.retry
      输入侧区分是好的：:450 user/bot 分列、:464-465 sent_messages(role=self)、群轮保留真实 subject
      但输出侧只剩软指令：:12 "不把机器人说的话当用户事实" 与 :477-479 "confirmed_reply/native_reply
      两者的有价值内容都要整理进摘要" 互相矛盾（后者更具体，实际生效）
      产物 = 自由文本摘要，无逐句 speaker 校验、无与 facts 的冲突比对
      ──► memory_summaries(:518) + memory_summary_people(:524, 该用户QQ被盖为出处)
      ──► core_note 同理（:382-411，:398 把用户QQ写进 people 出处）
   │
   ③ 检索回灌：memory_scope.py:46-49 summary_rows 跨 scope 桥接
      群摘要经 memory_summary_people 进私聊召回，标签仅 kind=conversation_summary_not_verified_facts
      → bot 当"用户说过"再断言 → 用户反驳 → 新轮次入素材 → 下次摘要合并（:447,:506 big merge）
      → small 原始材料被删，raw 证据被合并散文取代 → 闭环完成
   │
   [唯一出口] 手动：用户"更正记忆：/忘记："命令 → memory_reflection.invalidate(:59-86)
              或运营端 data_admin.py:86-119（edit_fact/forget_fact/forget_summary）
```

**生产实锤（behavior.sqlite，bot 7356b371）**：
- `memory_summaries` id=52（self 全局摘要）同段自相矛盾："**不知底层模型与Token来源**"与"**机器人曾告知暂切换GPT-6 Luna，修复后再换回**"并排；后者是 bot 自己的公告被当成事实整理进摘要。
- id=53（private:768687099）已把 09:54 能力事故固化："机器人承认此前对能力说法不准确"。
- `memory_summary_people` 把 id=52 桥接给十余个 QQ（含 768687099）——任何相关私聊都会拉到这段矛盾文本。

### 旁路通道

**C-1. motives 归到用户名下（bot 的话驱动主动私聊）**
- `companion_state.py:130-142`（outgoing）：对 bot 回复正文逐句正则 → `:138-139 INSERT INTO companion_motives VALUES(?,?,m[1],...,'open',...)`，subject 是**被回复的用户**。bot 说"下次我们聊聊X"→ 记成用户名下的未完话题。
- `companion_insights.py:71-75`：`source=evidence(item,'bot') or evidence(item,'user')`，取 bot 字段时 `person=source['sender_id']` 仍是该轮用户 QQ。
- `companion_schedule.py:95-96`：模型自拟 plan 的 topic 也写为 open motive（origin='model_plan'）。
- 效果：`companion_state.select_members:179` 给有 open motive 的成员 +10 分 → 主动私聊"上次说好聊X"，而用户从未说过。
- （生产数据：当前仅 1 条 open motive，evidence 指向用户 event，暂无实际受害记录，但通道是开的。）

**C-2. 机械 engaged（错误回复被当成功样例）**
- `participation.py:41-42`：180 秒内任何 directed 消息都把 dialogue_feedback 标 'engaged'，不看内容。用户质问"我啥时候说得"也是 directed。
- 生产数据：**engaged=118 / corrected=2 / unobserved=180**。模型判定的负反馈（companion_insights.py:106-118）几乎不触发。
- 回灌：`participation.py:49`（context）把最近 3 条 feedback（含 reply 全文+result）给模型当"过往回复效果" few-shot；`entry_factor:56-59` 只有连续两次 rejected 才降权。

**C-3. 自强化表达/兴趣**
- `social.py:179-186`（expression_sent）：bot 每次发送含 learned 表达即 `reinforce_expression(...,'use',...)` +0.05，无需任何用户反馈（有节流）。
- `companion_state.py:136`：bot 回复里提到的话题 → note_interest(origin='bot_expression')；用户礼貌陪聊 → conversation_heat(:115) 固化（memory_strength.py:15-16，hits>=3 固化 30 天）。
- 生产数据：companion_interests 已有 3 条 source=autonomous_choice（眼镜系列）、1 条 bot_expression（白粥）、1 条 conversation_heat（白饭，hits=5, weight=1.38）。
- 对照（正确实现）：social.py:150-152 学习需两人覆盖的硬校验；social_samples 只收群用户真实消息。

**C-4. facts 表（安全区，无自毒通道）**
- `hosted_memory.py:66-93`：四条写入通道全部与 bot 自身话语隔离（用户"记住："命令、self_report 整句正则 :21-36、companion_insights quote 必须逐字在 user 字段、data_admin）。生产数据 78 条 facts 抽查无归属错误。
- 用户侧 facts 抽查（饮食偏好/称呼/住宿方式等）内容与来源一致。

---

## 5. 生产病例清单（同类已发生）

| # | 时间(UTC) | bot | 症状 | 对应病类 |
|---|---|---|---|---|
| 1 | 11-06 16:15 | 小双白 | 群里说的话私聊说成"你刚说"，质问后二次脑补 | A1-A5 + C 主链 |
| 2 | 11-06 09:54 | 小双白 | "我先前说'本来就可以'也没依据，是我把能力记混了"（工具列表按会话变化，能力记忆漂移） | B2/C 固化（已进 summary 53） |
| 3 | 11-06 08:38 | 梁沅芷 | "把它说成'亲昵称呼'，又把其他约定混了进来。是我记错也说错了"（约定边界混淆） | A/B 同类 |
| 4 | 11-06 18:46 | 小双白 | "你刚才连着喊我小鲸鱼、问我在做什么"（实际只喊了一声）——时间/次数放大 | B3 时间脑补 |

另有结构性人格矛盾待处理：群公告"双白暂时把我换成 GPT-6 Luna"（bot 自述，已进 summary 52）与人格设定"不知底层模型与Token来源"（同在 summary 52）并存，用户群里那句"这是啥模型"撞的就是它。

---

## 6. 修复清单（按杠杆排序）

| # | 修什么 | 位置 | 治什么 | 验收 |
|---|---|---|---|---|
| 1 | 执行器归属护栏：system 增补 sourceScope 语义 + "引用其他会话内容必须写明来源（'你刚在群里说'），禁止'你刚说'类表述；sourceScope 缺失时先声明来源不确定" | autonomous_runtime.py:820-824 | 病例直修 | 复现场景（群说→私聊主动接话）输出含来源限定 |
| 2 | ActionIntentSchema 增加 sourceScope/sourceEventIds（同步 autonomous_runtime.py:579 回退 schema） | packages/contracts/autonomous.ts:36-42 | A1 | 模型可结构化提交出处 |
| 3 | commitDecision：objective 引用入站观察时 source 不得为空（可用启发式：objective 含引导词/引用时要求 sourceEventId） | packages/database/autonomous.ts:196-199 | A2 | 群→私聊目标落库带 source |
| 4 | node-agent 派发：跨会话目标（source.scope≠target）也附加 requestSource（或等价出处包） | apps/node-agent/src/autonomous.ts:211 | A3 | 同上 |
| 5 | summary_item 渲染 memory_summary_people/subjects 进条目 | memory_scope.py:53-57 | B1 | 跨 scope 摘要条目带参与者 |
| 6 | 摘要写后校验：归给用户的子句必须能在 user 字段/对应 event 原文找到；native_reply 退出摘要素材或强制 unverified 且不参与 coverage | memory_reflection.py:12,447-479 + summary_material.py:52-65 | C 主链 | 构造 bot 脑补样本，摘要不产生用户归属句 |
| 7 | motives 三处写入要求用户字段证据或加 speaker 列（bot_origin 的 motive 不加分或降权） | companion_state.py:138 / companion_insights.py:71-75 / companion_schedule.py:95-96 | C-1 | bot 自述话题不产生 open motive |
| 8 | 机械 engaged 排除负向情绪/质问类 directed 消息（可用 emotion_state 信号或最小长度/疑问式启发） | participation.py:41-42 | C-2 | 质问不产生 engaged |
| 9 | 统一时间锚点：social_context_view 保留 age_seconds（或统一渲染"X分钟前"）；Ambient 补 at | context_transport.py:51-55 + runtime_care.py:24-26 | B3、病例4 | 历史条目带可读相对时间 |
| 10 | experience_view 的 objective/reason 不截断归属限定语（或扩阈值） | context_transport.py:157-183 | B4 | 跨 scope objective 完整可见 |
| 11 | initiative 合成事件加平台来源标记，与 make_event sender='0' 约定对齐 | initiative.py:201-206 | A9 | 平台文案不被当成对方发言 |
| 12 | experience_context 对无 sourceScope 的历史条目降权或标记 unknown-origin | bot_pipeline.py:97-130 | A8 | null 出处不再无差别放行 |
| 13 | **存量清理**：用"更正记忆："/data_admin 重建 summary 52（消除"GPT-6 Luna"vs"不知模型"矛盾段）与 summary 53（能力事故段改写为带时间的事实） | data_admin.py:86-119 | 存量污染 | 摘要无自相矛盾段 |
| 14 | （可选）摘要/big-merge 前与 active facts 做一次说话人感知的冲突比对 | memory_reflection.py | C 主链闭环 | 矛盾摘要在合并前被拦 |

优先级建议：**1+2+6 三刀断根**（覆盖病例 1/2 的生成端与固化端），4/5/7/8/9 防复发，13 立即执行（人工，5 分钟）。

---

## 7. 附录：取证方法与数据位置

- 服务器：110.41.11.66（ssh root），运行时数据卷 `/var/lib/docker/volumes/fishbot-production-runtime-c/_data/`
  - `bots/<id>/runtime.log` — 协议收发日志（UTC 时间戳，二进制混排需 `grep -a`）
  - `bots/<id>/model-diagnostics.jsonl`（+`.previous.jsonl`，各滚存 256KB）— 每次模型请求/结果的 inputTokens/cachedTokens/prefixChars/firstDifference/响应ID
  - `bots/<id>/model-request-captures/<hex>/N.request.json|N.response.json` — 请求原文抓包（仅调试时段开启）
  - `bots/<id>/behavior.sqlite` — 记忆/行为库（70+ 表：facts、memory_summaries、memory_rounds、companion_*、pipeline_*、dialogue_feedback、social_* 等）
- 本病例相关行号快照：
  - pipeline_actions rowid=297（主动私聊决策+response 全文）
  - memory_rounds rowid=989、memory_round_material rowid=138（质问轮）
  - social_samples id=8415/8417、pipeline_quote_sources handle=qr_4566ddd0757ee1bb5ca0 / qr_631ca3594c77f7e3a4be（群里原话）
  - memory_summaries id=52/53、memory_summary_people（自毒固化）
  - dialogue_feedback 状态分布：engaged=118 / corrected=2 / unobserved=180
- 时间约定：runtime.log、behavior.sqlite 的 at 均为 UTC epoch；QQ 客户端显示北京时间。
- 审计覆盖：adapters/astrbot 约 50 个模块 + apps/node-agent + packages/contracts + packages/database；生产数据覆盖 11-06 全天（diagnostics 滚存限制，模型调用量化仅覆盖 ~21:00-23:32 窗口）。
