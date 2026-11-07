# FishBot 长期记忆质量与 QQ 收藏表情语义审计报告

- 审计日期：2026-11-07（数据快照时刻 2026-11-06T14:27Z / 2026-11-06 22:27 +08:00）
- 审计范围：`D:\MYCODE\APP\fishbot\qq-ai-saas` 源码 + Linux 生产容器只读数据
- 审计性质：只读调查、离线验证、问题归因。**未修改任何生产数据库、源码或配置；未部署、未重启容器；未向真实 QQ 发送任何消息；未调用生产模型做新对话实验；未新增/删除/重新生成任何长期记忆。**
- 全部取证脚本位于 `.local/audit-20261107/`（gitignore 内），通过 SSH 只读通道执行（`mode=ro` 打开 SQLite，Postgres 只跑 SELECT）。
- 报告内 QQ 号、私聊正文按默认脱敏处理，只保留可复核的最小片段。

---

## 0. 版本口径（必须先分清）

| 口径 | 实体 | 提交/标识 | 说明 |
|---|---|---|---|
| **当前生产运行版本** | 容器 `fishbot-production-{api,agent,gate}`，镜像 `fishbot/qq-ai-saas:0.1.0-security-r3` | `app-final-b/deployed-source.json` → `7ef595f9…` + 18 个运行时对齐文件 | 容器实际挂载 `/opt/fishbot-pg-cutover-20261031/app-final-b/`（只读）。**这才是被审计对象。** |
| 生产数据卷 | `fishbot-production-runtime-c` → `/var/lib/fishbot` | 每 bot 一份 `bots/<id>/behavior.sqlite` | 控制面在 `fishbot-production-pg`（Postgres 17.6） |
| 生产源码检出（非运行） | `/opt/fishbot-monitor-20261031/source-commit` | `bf172db`（含未提交热修） | **不等于**运行代码，不能拿它当生产实现依据 |
| Windows 工作区 | `D:\MYCODE\APP\fishbot\qq-ai-saas` | `main` = `e0d8043`，工作区有大量未提交改动 | 与生产逐文件比对：61 个 Python 适配器中 **57 个字节一致** |
| **未部署候选版本（R4）** | 工作区与生产不一致的 4 个文件 | `behavior.py` / `context_transport.py` / `model_cache.py` / `output_clean.py` | 见 §C-01，其中 `context_transport.py` 属**高风险未部署改动** |

比对方法：对 `app-final-b/adapters/astrbot/*.py` 与工作区同名文件逐一 MD5 比较（61 个文件，57 一致、4 不一致）。因此本报告中凡是标注「生产」的行为，均以 `app-final-b` 版本源码 + 生产数据为准；工作区独有改动一律标注为「未部署候选」。

---

## A. 当前能力总览

| 能力 | 已实现 | 生产已使用 | 效果已验证 | 主要问题 |
|---|---|---|---|---|
| 长期事实提取 | 是（`hosted_memory.ScopedMemory.remember`） | 是（8 bot 共 182 条 active） | 部分（有真实召回证据） | 事实通道 86% 不是人物事实；名称由模型自定，无合并语义 |
| 重要性筛选 | 弱（仅 `importance=1.0/0.8/0.5` 按名称/kind 固定） | 是 | 否 | 没有内容级重要性判断，重要性不参与准入只参与轻微加权 |
| 长期记忆纠错 | 部分（版本号 + `active` + `memory_blocks` + `invalidate()`） | 是 | **否**（见 C-03：纠错只落到摘要层，事实层未失效） | 只识别 `更正记忆：`/`忘记：` 显式指令；自然纠正不触发 |
| 偏好持续更新 | 部分（同名覆盖 + 版本递增，保留 10 版） | 是 | 部分 | 语义同名不同键不合并（`食物偏好` vs `饮食偏好` 并存） |
| 摘要合并 | 是（小摘要→大摘要强制 rollup） | 是 | **是（已证明信息流失）** | 5 天内摘要总量 4500→893 字（−80%），原始轮次被删除 |
| 重要约定召回 | 是 | 是 | **是**（抓包证明「摸头边界」「群聊发言边界」进入请求） | 召回排序是词重叠+时间，无真相关性；超预算后按更新时间取，不按重要性 |
| 人物身份隔离 | 是（`person_filter` 按协议 QQ 号） | 是 | 部分 | 全局大摘要跨 scope 注入到所有人（C-04） |
| 未完成任务记忆 | 是（`autonomy_goals` + `pipeline_actions.unfinished` + `companion_motives`） | 是 | 部分 | 目标长期 `active` 不复核（7356b371 有 11-05 的陈旧 active 目标） |
| 收藏表情资源 | 是（index.json + images/） | 部分（8 bot 中仅 3 个有索引） | 是 | 5 个 bot 完全没有索引；无全量同步标记 |
| 表情语义标注 | 是（视觉模型标注 `meaning/avoid/confidence`） | 是 | 部分 | 53 张中仅 26% 有视觉标注；`uncertain` 永不重试；QQ 原文直接当语义 |
| 表情语义检索 | **否** | 否 | — | 无向量、无 FTS、无排序；只能 `list` 全量枚举；`meme_candidates` 路径因 `index.enabled=false` 关闭 |
| 表情选择与发送 | 是（`prepare` → 随回复投递 → `message_id` 回执） | 是（2 bot 共 11 次真实使用） | 部分 | `prepare` 成功≠送达原图；存在同一张图重复准备 |

---

## B. 最重要的六个结论

### B-1 长期记忆中有多少是真正重要的信息？

**结论：真正的人物关键信息确实存在且质量不低，但只占长期事实通道的 19.8%；其余 80% 是机器人自己的研究目标与一次性迁移导入。等级：已证实。**

分母与方法：8 个有 `behavior.sqlite` 的 bot，`facts` 表 `active=1` 共 **182 行**（全量，非抽样）。

| 分类 | 条数 | 占比 | 判定依据 |
|---|---|---|---|
| `自主研究:` 自主决策目标/结果（`origin='autonomous_result'`，scope=`self`，subject=`0`） | 110 | **60.4%** | 内容是「用 inspect_context 读取当前兴趣…」「读取 BV1zsKmzAEGp 第1P字幕…」，是机器人的任务计划与工具过程，不是人物信息 |
| `迁入·` 旧系统一次性导入（2026-11-04） | 47 | **25.8%** | 含 `迁入·原文要点01–19`，是把旧系统长文按 200 字切块存成"事实" |
| 人物 scope 事实（`subject` 为真实 QQ 号） | 36 | **19.8%** | 唯一真正属于"人物长期事实"的部分 |
| 其中属于人物「关键长期信息」 | 约 20 | 约占人物事实 55% | 见下表 |

人物事实逐条质量抽样（36 条全查，以下为分类结果）：

- **关键长期信息（约 20 条）**：`摸头边界=别让别人摸头`、`群聊发言边界=没有我的同意，你不可以在群聊里聊天`、`主动联系偏好=你可以多找我`、`迁入·相处约定`、`迁入·身份与生日`、`迁入·亲属`、`迁入·人物边界`、`性别`、`称呼`、`互动偏好=不要老对我发问` 等。这些确实承载身份、边界、约定、关系，质量可用。
- **短期/瞬时信息（7 条，19%）**：`安排=去啃一会C语言，一会有不会的要问`、`活动=去跑乐跑`、`饥饿感=不太饿`、`课程安排=去上水课`、`行程=上水课`、`正在做的事=补工程制图的作业`、`当前状态=做客`。全部由摘要模型的 `state` 类抽取写入，`kind='state'`（2 天半衰期）或误标 `stable`。
- **重复/碎片（5 条）**：`食物偏好=肉` 与 `饮食偏好=不爱吃苦瓜` 并存；`课程安排` 与 `行程` 语义重复；`迁入·人物特点` 出现 2 条同键不同人。
- **过时/相对性表述（2 条）**：`旧关系=这个时期我们的关系闹得很僵`（`kind='event'`，半衰期约 90 天，会被当作当前事实反复召回）；`三盆白饭交易=三盆白饭已经谈好了`。
- **可确认错误（0 条）**、**敏感越界（0 条，未见凭据/密码，`scrub()` 与写入侧正则有效）**。

**核心回答**：模型并没有"把大量闲聊细节当成长期事实"——`companion_insights.apply` 有严格接地约束（`quote` 必须是用户原话子串、必须含「我/本人」、拒绝 `他说/假设/主人/权限` 等）。真正的问题是 **长期事实通道被机器人自身的研究目标挤占**（60.4%），以及**瞬时状态被当作长期事实入库**。而「真正重要的人物约定」并没有被忽略——它们被正确提取了，只是被淹没在 182 条里的 36 条之中。

摘要侧同样偏薄：8 bot 合计 **15 条摘要、4908 字**，覆盖 3148 条事件引用。平均一条摘要承载约 210 个事件引用。

---

### B-2 错误信息能否被正确修正，还是旧错误会反复出现？

**结论：纠错机制存在，但只作用在"摘要/核心便签"层，不作用在"事实"层；旧事实会与新的纠正同时进入模型上下文。等级：部分证实。**

机制盘点（`memory_reflection.invalidate` + `hosted_memory`）：

| 场景 | 现有处理 | 实际效果 |
|---|---|---|
| A 直接纠正「不是A是B」 | 只有用户显式输入 `更正记忆：A=B` 才触发 `invalidate()` + 同名覆盖 | 自然语言纠正**不触发**；同名覆盖只在新事实 name 完全相同时发生 |
| B 信息自然变化 | 同名新值 → 旧行 `active=0`，保留 10 个版本 | 生效，但需要模型自己起同一个 name |
| C 不断细化 | 无合并；产生并列碎片 | `食物偏好`/`饮食偏好` 并存即实证 |
| D 否定错误推断 | 只有 `忘记：X` 会写 `memory_blocks` 并 `active=0` | 需显式指令；生产仅 1 条 block（`d912eed3` 的 `自我认同`） |
| E 第三方 vs 本人 | `facts.origin` 区分 `explicit/self_report/autonomous_result`；`self_report` 不允许覆盖 `explicit` | 方向正确，但**摘要层不区分来源可信度** |
| F 明确遗忘 | `invalidate()` 删 `memory_summaries/memory_rounds/memory_pending/memory_core_notes`，`epoch+1` 使在途摘要作废 | 实现较完整；但 `memory_summary_coverage` 由触发器删除，未见悬挂 |

**生产实证（关键反例）**：

```
FACT  scope=group:1131335806 subject=3879****54 name=称呼 value=江哥
      updated=2026-11-06 17:02  version=1  active=1     ← 仍然有效
CORE  persona_core_note updated=2026-11-06 21:06
      "…别人摸头、要求亲近称呼一律先回绝…不叫他人'老大/江哥'…"   ← 已纠正
BIG   memory_summaries id=29 (517字) "核心纠正：…不叫他人'老大/江哥'…"
```

同一条约束在**两个通道里互相矛盾**，且两者都会被注入：事实通道说「这个人的称呼是江哥」，摘要/便签通道说「不许叫任何人江哥」。模型看到的是同等可信的两份数据。

同类证据：`2026-11-06 16:46` 存下的 `自主研究:` 事实自述「本次查到的资料没有证据表明阿梁把'老大'列为禁忌」——说明当时的检索没有命中纠正，而纠正直到 21:06 才写进 core_note。事实层从未被回溯修正。

**纠错成功率**：可明确判定正确答案的样本只有 1 例（江哥）。事实层纠正 0/1，摘要/便签层纠正 1/1。样本量不足，**不给出百分比**。可确认的冲突残留率：8 bot 全库扫描，`memory_blocks`=1，无同名多版本冲突（因为同名覆盖生效），但**跨通道冲突 ≥1 例已确认**。

---

### B-3 重要信息是否真的进入模型最终上下文？

**结论：进入。已用真实 HTTP 请求抓包在「最终请求体」层证明。等级：已证实（同时发现一处跨 scope 越界，见 C-04）。**

验证链（五级）：

| 级别 | 验证方式 | 结果 |
|---|---|---|
| 1 数据库存在 | SQLite 直读 | 是（182 条 facts、15 条摘要、4 条 core_note） |
| 2 检索器选中 | 用生产源码 + 生产 DB 离线复算 `fact_rows/recall/semantic_context` | 是 |
| 3 ContextBuilder 装入 | `bot_pipeline.conversation_context` → `semantic_context.recall` → `ContextBuilder.build` | 是 |
| 4 **最终 HTTP 请求真实包含** | `bots/<id>/model-request-captures/*.request.json`（真实发出的 body） | **是** |
| 5 模型正确使用 | 只有真实回复文本可作证据 | 部分（见下） |

第 4 级实证（`d18ad07d`，`2026-11-06 19:02:14`，`callKind=event`，48 条消息 / 47732 字符）：

```
facts_in_request = 11
  - 摸头边界        = 别让别人摸头                      [recent_fact]
  - 群聊发言边界     = 没有我的同意，你不可以在群聊里聊天      [recent_fact]
  - 点餐偏好 / 担心被不爱 / 迁入·余额约定 / 迁入·相处约定 / 迁入·称呼与身份   [recent_fact]
  - 旧关系 / 迁入·运动与宿舍经历 / 迁入·亲属 / 迁入·学业                    [model_semantic_candidate]
msg[0] role=system  7615 字符，含 persona_core_note 与 rolling_background 标记
msg[1] role=user   80270 字符，列式表：state.facts(11) + state.conversation_summaries(1×517字, 当时 id=25)
```

> 注：该请求在 19:02 组装，当时 `memory_summaries` 的 big 是 **id=25**；21:06 的 rollup 把它删掉并写成 **id=29**。所以用当前 id=29 的文本去检索这批抓包会"查不到"，属时间错位，不是未注入。

第 4 级实证（`7356b371`，`2026-11-06 19:47:08`，38 条消息 / 50221 字符）：
`facts=3`（`饮食偏好/主动联系偏好/食物偏好`），只读资料包 19768 字符，后续为 `readonlyChanges` 增量（4298 / 7439 字符）。

**重要约定确实进入了模型最终请求**：owner 的两条硬约束（摸头边界、群聊发言边界）在私聊请求中以 `recent_fact` 身份出现在 `state.facts` 里；`persona_core_note` 与滚动大摘要进入 system。第 5 级（模型是否据此正确行动）有间接反证：`2026-11-06 19:02` 的回复「你说过不让别人摸头，我还是口头答应了；你说未经你同意不准在群里聊天，我之前也没守住」——说明模型读到了并承认违背过，但**违背发生在本轮之前**，不能据此证明约束在违背当时是否在场。

**关键缺口**（同属第 4 级证据）：
- 摘要的序列化不是明文列表，而是 `decision_wire.py` 的列式记录表（`kind/columns/rows/common`，相同字段提升到 `common`，首列 `#` 作资料编号，`contextRecordRef` 反向引用）。这节省 Token，但意味着**模型读到的是压缩后的表结构**，需要正确还原表头才能理解。生产实测：`d18ad07d` 的 `state.facts`（11 条）与 `state.conversation_summaries`（1 条，517 字）都以这种表形式出现在 `user` 只读资料包内（该请求体 80270 字符）。
- `recall()` 输出的同 scope 摘要会被 `r[4]!=scope` 过滤掉，只剩 `rolling()` 的 1 条大摘要 + 3 条**最旧**小摘要。当前每个 bot 只有 1–3 条摘要，尚未造成可见损失；一旦摘要条数增长，同 scope 的中间区间将不可召回（见 C-11）。
- 全部 8 bot 的 `embeddingModel` 均为空字符串 → `hosted_memory.semantic_recall` / `memory_reflection.semantic_recall` / `social.context_semantic` 全部在入口即 return fallback。生产 `memory_vectors=0`、`fact_vectors=0`（全库），**向量语义召回从未运行过一次**。
- 实际生效的"语义召回"是 `semantic_context.recall`：FTS5 词匹配 + 全部 facts（`limit=16`，1600 字符预算）+ 最近 8 条摘要，打上 `model_semantic_candidate` 标签交给回复模型自己挑。

---

### B-4 摘要合并是否持续改善人物理解，还是造成信息流失？

**结论：造成可测量的信息流失。等级：已证实（流失）；是否改善人物理解：未知（无对照评测，不允许编造）。**

纵向对照（同一 bot `7356b371`，两个真实时间点）：

| 时点 | facts | 摘要条数 | 摘要总字数 | 结构 | memory_rounds |
|---|---|---|---|---|---|
| 2026-11-01 03:29（`/opt/fishbot-companion-full-20261101/backup-032939`） | 3 | **15** | **4500** | 无 level 表（旧结构） | 100 |
| 2026-11-06 22:27（生产卷） | 69 | **2** | **893** | big:1 / small:1 | 76 |

5 天内摘要总量下降 **80.2%**，而对话量在增加。原因在代码里是确定的：

1. `memory_reflection.retry()`：`counts[small]>=3 or counts[big]>1` 即触发 big rollup（`:326-331`）。
2. big 任务的输入 = 全部 big + **最旧 3 条** small（`:330-331`），输出一段 **≤1500 字**（`:350` 提示词 + `:392` 截断）。
3. 成功后 `DELETE FROM memory_summaries WHERE id IN (…)`（`:411`）删除全部输入摘要，只留 1 条新 big。
4. 同时 `DELETE FROM memory_rounds WHERE scope=? AND id<=?`（`:433`）删除已摘要的原始轮次。

因此每个 bot 最终只剩 **1 条 big + 少量 small**，全部原始轮次被物理删除。当前生产：

| bot | 摘要条数 | 摘要字数 | 覆盖事件引用数 | 覆盖率 |
|---|---|---|---|---|
| d18ad07d（梁沅芷） | 1（big） | **517** | 773 | coverage 411 行全指向这 1 条 |
| 7356b371（小双白） | 2（big 673 + small 220） | 893 | 1668 | 67 |
| 99ba4892 | 2 | 613 | 331 | 211 |
| 4d7ba344 | 3 | 949 | 212 | 30 |
| 693475e2 | 3 | 1277 | 111 | 0 |
| 31fc5cf3 | 3 | 480 | 53 | 0 |
| 9605fc81 | 0 | 0 | 0 | 0 |
| d912eed3 | 0 | 0 | 0 | 0 |

即：`d18ad07d` 用 **517 个汉字**承载 773 条真实事件引用；`7356b371` 用 **893 字**承载 1668 条。原始材料已删除，无法回溯。

补充机制：`summary_material.project_history`（`:13-27`）会把**已被摘要覆盖的原生历史轮次从模型请求中移除**（`memory_summary_coverage` 提供逐轮摘要+回复的双重摘要证明后才移除）。所以 35 轮窗口在"已摘要"区间会进一步缩短——`historyTurns=35` 只是原生历史上限，不等于模型看到的真实轮次。

---

### B-5 QQ 收藏表情语义到底完成到哪一层，实际准确性如何？

**结论：完成到「资源 + 全量枚举 + 部分视觉描述」，语义索引层未实现，语境选择依赖回复模型自由判断。等级：L1–L2 已证实，L3 部分，L4 未实现，L5 部分（无法量化准确率），L6 已证实存在但有已知失败模式。**

逐层证据：

| 层 | 是否实现 | 代码 | 生产证据 |
|---|---|---|---|
| 1 保存/枚举资源 | **是** | `favorite-maintenance.ts:23-48`（`fetch_custom_face_detail(count=200)` → 下载 → `favorite_media.normalize` 归一化 → `images/`）、`qq-favorite-index.ts`（原子写 index.json） | 3 个 bot 有索引：27 / 15 / 11 张，共 **53 张**，images 目录文件数与之完全一致（27/15/11），gif 8/7/8 |
| 2 关键词查找 | **部分** | `qq_favorite_tools.py:181-184` `operation='list'` 返回全部（≤200）带 `meaning` 的条目 | 有；但这是**全量枚举**，不是检索。没有按词/标签过滤的入口 |
| 3 准确文字描述 | **部分** | `qq-favorite-semantics.ts:23-61`（`favorite_media.preview` 生成 0/中间/末帧拼图 → 视觉模型输出 `description/meaning/avoid/confidence`） | 53 张中 **14 张有视觉标注（26%）**；其中 `confidence=clear` 仅 4 张；`uncertain` 10 张；`pending` 28 张；`failed` 6 张 |
| 4 语义索引 | **否** | 无向量、无 FTS、无排序表。`social.catalog` 会把 `qq_favorites.catalog()` 并入候选，但 `qq_favorites.catalog` 要求 `index.enabled===true` | 全部 3 个索引的顶层 `enabled=false` → **收藏表情从未进入 `social.meme_candidates`**（`meme_usage` 里 `bf_` 平台图包 43 次 vs `qf_` 收藏 11 次，且 `qf_` 全部来自工具 `prepare` 路径） |
| 5 语境/情绪选择 | **部分** | 靠回复模型读 `meaning`/`avoid`；`meme_reactions.categories/relevance` 只服务平台图包（有 `category` 字段），收藏条目没有 `category` | `memeSemantic=false`、`embeddingModel=''`（全部 bot）→ `social.context_semantic` 直接 fallback。**无法计算准确率** |
| 6 真实送达 | **是** | `qq_favorite_tools.prepare` → `event.set_extra('_hosted_tool_sticker', bytes)` → `behavior.py:826` 装配 → `:861` 记 `_hosted_favorite_wire` → `:395` 给 image 段打 `sub_type=1`（QQ 自定义表情标记）→ `message_id` 回执 | `meme_usage` 记录 `qf_` 使用 11 次（7356b371 1 次、d18ad07d 10 次）；`pipeline_action_effects` 有 `send_private_msg/state=confirmed/receipt=<message_id>` |

**语义准确性抽样**（按实际库存全查 53 张，人工审阅其 `meaning/avoid` 文本）：

- 高质量（能区分画面与聊天含义、带 `avoid`）：4 张。例：`可表达疲惫、失落、无奈或提不起劲…` / `avoid: 不适合表达明确的开心、兴奋或严肃事实`；`适合表达开心、满足、赞同或轻松回应…` / `avoid: 不适合表达严肃拒绝、愤怒、悲伤或正式道歉`。
- **低质量（把 QQ 原始 OCR/描述直接当语义，且因此被 `enabled=true`）**：5 张。例：`你很牛逼0`、`解书`、`你在干嘛`、`知识又增加了Giao`、`气哭`。
- 无描述（禁用）：44 张。

**为什么低质量那 5 张不会被改进**：`favorite-maintenance.ts:43` 写入时 `meaning = source.modifyWord||desc||ocrWord`，`enabled = !!meaning`；而 `:50` 的补标注队列条件是 `!i.meaning`。**只要 QQ 侧给了任何文字，这张图就永久跳过视觉标注并以该文字作为语义启用。**

**GIF 处理**：`favorite_media.normalize` 对动图按最多 48 帧重采样并保留动画；`preview` 只取第 0 / 中间 / 末帧拼成一张 192px 三联图交给视觉模型。**标注只看到 3 帧**，对"动作过程/关键帧"覆盖有限，但不会误用单张静态帧（`sampledFrames` 会写回）。属于"部分正确"。

**已知失败模式（生产自证）**：`d18ad07d` 的回复原文「我刚才不该把同一张'不想说话'的收藏准备两次」「虽然有一次选表情得到确认，但那只说明选中了收藏，不足以证明发出去的一定是你指定的原图」——说明 (a) 同一收藏可被重复 `prepare`；(b) `prepare` 确认与真实投递是两件事，系统设计上已承认这点。

---

### B-6 目前还有哪些机制最明显地限制机器人智能表现？

按实测影响排序：

1. **`dailyProactiveLimit`（默认 10）是"每 bot 全局共享"的主动消息预算，被群聊提前耗尽后，机器人在当天剩余时间无法主动联系任何人。** 这是「小双白不来找我/不去群里」的直接根因（见 §G）。证据：`7356b371` 2026-11-06 的 `deliveries` = `group:963105233` 6 次（最后 11:53）+ `private:768687099` 4 次（最后 11:50）= 10，此后 `autonomous_runtime.candidates()`（`:362`）与 `initiative.candidates()`（`:46-47`）全部返回空列表。
2. **外层自主决策空转**：近 2 天 `7356b371` 的 1048 次 cycle 中 **560 次（53.4%）以 `model_request_unconfirmed` 结束**——决策模型请求没有拿到确认，等于没有决策。`d18ad07d` 另有 335 次 `request_token_budget` + 2 次 `daily_request_token_budget`。
3. **长期叙事坍缩**：1 条 ≤1500 字大摘要承载全部历史，原始轮次被删（B-4）。
4. **长期事实通道被自身研究目标占满**：60.4% 是 `自主研究:`，且每条占用一个独立 name（`d18ad07d` 的 `self/0` 已用 71 个 name，上限 200）。
5. **召回无真相关性排序**：`hosted_memory.recall` = 词重叠 + IDF 加权 + 时间；`semantic_context` 把 16 条 facts 当"候选"塞给模型。`embeddingModel` 全空 → 向量召回从未启用。
6. **全局大摘要跨 scope 注入**：`memory_reflection.rolling()` 不按 scope 过滤，导致 `scope='self'` 的全局大摘要进入所有人的会话（C-04）。
7. **表情语义索引缺失**：收藏表情只能全量枚举，且 83% 未启用；无法按语境检索。

---

## C. 逐问题证据表

> 严重度：P0=直接损害核心能力/边界；P1=显著影响长期记忆质量；P2=浪费上下文或降低精度；P3=观测不足。
> 「对护栏/身份/记忆/缓存的影响」指对 35 轮护栏、身份边界、记忆正确性、缓存前缀稳定性的影响。

### C-01（P0，未部署候选）工作区 `context_transport.py` 把身份与权限从 system 消息降级为 user 消息

- **受影响范围**：所有 bot（一旦部署）
- **代码路径**：`adapters/astrbot/context_transport.py::request_context_view`
  - 生产（`app-final-b`，:121）：`authority_packet={'role':'system','content':AUTHORITY_CURRENT+…}` → 身份/权限以 **system** 角色下发
  - 工作区（未部署，:119-122）：`event_packet={'role':'user','content':EVENT_CONTEXT_CURRENT+… 'authority':authority,'policy':policy,'currentGuidance':current_rules…}` → 身份/权限以 **user** 角色下发
- **真实证据**：61 个适配器 MD5 比对，仅 4 个不一致；`context_transport.py` 生产 20115 字节 / 工作区 19783 字节，`request_context_view` 函数体整体重写。生产抓包 `system[29]/[33]` 可见 `[FishBot 本轮执行身份与规则 v1]` 确实是 system 角色。
- **复现方法**：`md5sum app-final-b/adapters/astrbot/context_transport.py` vs 工作区；或读两份源码同名函数。
- **已证明影响**：部署后"可信身份/权限"与用户可写的 user 轮次处于同一角色层级，与用户既定约束「不降低可信身份及执行权限边界」「不尝试 developer 角色方案」直接冲突。
- **尚不确定**：是否已有意为之（`AUTHORITY_GUIDANCE` 文本改成了"由平台生成的数据包提供"）。但角色降级本身是客观事实。
- **最小修复方向**：保留生产版本；如必须改，身份/权限继续走 system，只把"只读资料"放 user。
- **对护栏/身份/记忆/缓存的影响**：**身份边界直接降级**；system 字节变化会使缓存前缀失效（预期内，但需按 35 轮检查点执行）。

### C-02（P0）主动消息预算全局共享，群聊消耗后 owner 当天无法被主动联系

- **受影响范围**：全部开启 `proactive` 的 bot；已实测 `7356b371`、`d18ad07d`
- **代码路径**：`adapters/astrbot/autonomous_runtime.py:361-362`、`adapters/astrbot/initiative.py:46-47`、`:105-106`、`adapters/astrbot/autonomy.py:312-313`（`owner_check_candidate` 也用同一个 `deliveries` 总和）
- **真实证据**：`7356b371` 2026-11-06 `deliveries`：`group:963105233` n=6 last=11:53，`private:768687099` n=4 last=11:50，合计 10 = `dailyProactiveLimit`。此后 13:14–14:35 的 cycle 全部 `action=null, wait=time`，理由文本为「当前没有可联系会话」「没有可用的消息对象」。
- **复现方法**：读 `bots/<id>/behavior.sqlite` 的 `deliveries` 表当日总和，与 `bot_config_revisions.config.behavior.dailyProactiveLimit` 比较；再看 `autonomy_cycles.decision` 是否全为 `action:null`。
- **已证明影响**：主动联系能力被"先到先得"地消耗；模型在 `snapshot()` 中拿不到任何"预算已耗尽"字段（`:408-423` 的 key 列表里没有 budget），于是把"候选为空"解释成"没有合适的对象/不该打扰"，产生**合理化叙事**。
- **尚不确定**：产品意图是否为"每 bot 每天最多 10 条主动消息（含群）"。若是，则不是 bug 而是策略，但**策略缺少"给主人的保留额度"**。
- **最小修复方向**：`deliveries` 按 scope 类别拆分计数（owner 私聊单独配额），或把 `dailyProactiveLimit` 改成"群聊配额 + 主人配额"两项。
- **对护栏/身份/记忆/缓存的影响**：不触及身份与记忆；会改变主动消息频率（需用户确认额度）。

### C-03（P1）纠错只落在摘要/便签层，事实层旧值仍 active 并同时注入

- **受影响范围**：`d18ad07d` 已确认 1 例；机制性问题影响所有 bot
- **代码路径**：`adapters/astrbot/hosted_memory.py:95-114`（`observe` 只识别 `记住/更正记忆/忘记` 正则）、`memory_reflection.py:59-79`（`invalidate` 只清派生层）
- **真实证据**：`facts(group:1131335806, 3879****54, 称呼)='江哥' active=1 updated=11-06 17:02`；`memory_core_notes updated=11-06 21:06` 含「不叫他人'老大/江哥'」；`memory_summaries id=29` 含同一纠正。
- **复现方法**：`SELECT * FROM facts WHERE name='称呼' AND value='江哥'` 与 `SELECT text FROM memory_core_notes` 对读。
- **已证明影响**：同一约束两通道矛盾；该人物再发言时 `称呼=江哥` 会以 `recent_fact` 进入请求。
- **尚不确定**：模型在实际回复中是否真的因此叫错（无对应抓包回复证据）。
- **最小修复方向**：纠正时同时 `UPDATE facts SET active=0`（按 name/值模式），或在 `facts` 与 `core_note` 之间加一层"当前有效口径"校验。
- **对护栏/身份/记忆/缓存的影响**：提升记忆一致性；会改动 `facts` 内容，需用户授权。

### C-04（P0）全局大摘要跨 scope 注入到无关用户的会话

- **受影响范围**：所有 bot（只要存在 `scope='self'` 的 big 摘要）
- **代码路径**：`adapters/astrbot/memory_reflection.py:257-261` `rolling()` —— SQL 为 `WHERE l.kind IN ('big','small') OR s.scope=?`，**big/small 不过滤 scope**；随后 `semantic_context.py:36-38` 把 `rolling` 无条件并入 `conversation_summaries`。
- **真实证据**：`7356b371` big 摘要 id=52 `scope=self`（673 字，含「QQ 7686****99（双白/主人）」「尚未解决：…激活码无法联网处理」等私聊内容），在 `private:2444309491`（**非主人**）的真实请求 `ee3b8d24/0.request.json` 与 `/3.request.json` 中 `present=True`。
- **复现方法**：取该 bot 任一 `callKind=event` 且 `conversation_environment.scope` 非主人的抓包，在消息体中检索 big 摘要前 30 字。
- **已证明影响**：私聊/他人的长期叙事进入第三方会话的模型上下文；与源码注释「跨会话记忆的 source_scope 是来源，不是当前现场」以及 `memory_scope.py` 的隔离意图矛盾。
- **尚不确定**：模型是否真的对外泄露（无泄露性回复证据）。属"上下文越界"而非"已发生泄露"。
- **最小修复方向**：`rolling()` 增加 scope 过滤（`s.scope=? OR s.scope='self'` 仅对主人/self 场景放行），或把全局大摘要拆成"per-person 摘要 + 机器人自述摘要"。
- **对护栏/身份/记忆/缓存的影响**：改善 scope 隔离；改变 system 字节 → 缓存前缀失效（预期内）。

### C-05（P1）摘要强制 rollup 导致信息流失且不可回溯

- **受影响范围**：全部 bot（`9605fc81`/`d912eed3` 尚未产生摘要）
- **代码路径**：`memory_reflection.py:326-331`（触发条件）、`:348-351`（提示词 ≤1500 字）、`:398-412`（删除输入摘要）、`:433`（删除原始轮次）
- **真实证据**：`7356b371` 11-01 15 条/4500 字 → 11-06 2 条/893 字；`d18ad07d` 1 条 517 字承载 773 条事件引用。
- **复现方法**：对比 `/opt/fishbot-companion-full-20261101/backup-032939/sqlite/<bot>/behavior.sqlite` 与生产卷同名表。
- **已证明影响**：细节、来源链、多轮修正过程在 big rollup 后不可恢复；`memory_summary_coverage` 只保留"该事件已被摘要覆盖"的证明，不保留内容。
- **尚不确定**：摘要质量本身是否足够（无人工对照评分）。
- **最小修复方向**：big rollup 后保留被合并的 small 摘要（标记 `superseded` 而非删除），或在删除前把 `memory_rounds` 归档到只读表。
- **对护栏/身份/记忆/缓存的影响**：直接改善长期记忆；会增加库体积（需评估 128MB 堆限制）。

### C-06（P1）`自主研究:` 目标以独立 name 写入事实表，占用人物事实额度与上下文预算

- **受影响范围**：`7356b371`（61/69）、`d18ad07d`（35/88）、`d912eed3`（7/8）、`4d7ba344`（5/12）等
- **代码路径**：`autonomous_runtime.py:893-901`
- **真实证据**：`facts.name` 形如 `自主研究:用 inspect_context 先看一眼自己当前的真实兴趣列`，`origin='autonomous_result'`，`kind='event'`；全库 110 条（60.4%）。
- **复现方法**：`SELECT count(*) FROM facts f JOIN fact_metadata m … WHERE m.origin='autonomous_result' AND f.active=1`
- **已证明影响**：`remember()` 的 `person>=200` 上限按 `(scope,subject)` 计；`self/0` 已用 71 个 name，接近上限；`total>=20000` 为全局上限。同时这些事实在 `recall('self','0','自主研究')` 中因全部命中 token「自主/主研/研究」而等价竞争，只能靠时间排序取 3 条。
- **尚不确定**：是否已实际导致 `self/0` 写入失败（当前 71 < 200）。
- **最小修复方向**：改用独立表（如 `autonomy_experience`）或单一 name + 覆盖写；或把 name 固定为 `自主研究`，把 objective 放进 value。
- **对护栏/身份/记忆/缓存的影响**：释放事实额度与上下文预算；不影响人物记忆。

### C-07（P1）瞬时状态被写入长期事实且 `stable` 类永不衰减

- **受影响范围**：`4d7ba344`（7/7 人物事实全是瞬时状态）、`7356b371`、`d912eed3`
- **代码路径**：`companion_insights.py:50-63`（`kind` 由摘要模型给，默认 `'stable'`）、`memory_strength.py:22-24`（`stable` 恒为 1.0）
- **真实证据**：`安排=去啃一会C语言`、`活动=去跑乐跑`、`饥饿感=不太饿`、`课程安排=去上水课`、`行程=上水课`、`正在做的事=补工程制图的作业`、`当前状态=做客`、`三盆白饭交易=三盆白饭已经谈好了`。
- **复现方法**：`SELECT f.name,f.value,m.kind FROM facts f JOIN fact_metadata m … WHERE f.subject!='0'`
- **已证明影响**：这些条目按"最近更新"优先进入 `recall()` 的 fallback 与 `semantic_context` 的 16 条候选，挤占 1600 字符预算。
- **尚不确定**：它们是否真的把关键事实挤出预算（当前人物事实总数小，尚未触发）。
- **最小修复方向**：对 `kind` 增加"时效白名单/黑名单"（`饥饿感/正在做的事/行程/活动` 强制 `state`）；`stable` 增加衰减或需二次确认才升 stable。
- **对护栏/身份/记忆/缓存的影响**：提升记忆精度；不改身份。

### C-08（P1）向量语义召回配置为开但从未运行

- **受影响范围**：全部 8 bot
- **代码路径**：`hosted_memory.py:146-150`、`memory_reflection.py:203-208`、`social.py:252-254`（三处均 `not model` 即 return fallback）
- **真实证据**：`bot_config_revisions.config.behavior.embeddingModel == ""`（全部 bot），`memorySemantic=true`；生产 `memory_vectors=0`、`fact_vectors=0`（8 bot 全库）。
- **复现方法**：读配置 + `SELECT count(*) FROM memory_vectors` / `fact_vectors`
- **已证明影响**：`memorySemantic` 开关是"死开关"；所有"语义召回"实际由 `semantic_context` 的 FTS+全量候选代替。
- **尚不确定**：是否有意留空（避免外部 embedding 调用成本）。`dailyEmbeddingLimit=100` 已配置，说明曾计划启用。
- **最小修复方向**：要么填 `embeddingModel`，要么把 `memorySemantic` 默认改 false 并在 UI 说明"未配置模型时该开关无效"。
- **对护栏/身份/记忆/缓存的影响**：纯精度/成本取舍，不影响身份与 35 轮。

### C-09（P1）收藏表情语义索引缺失 + 低质量语义被永久启用

- **受影响范围**：`d18ad07d`（4/4 启用项为 QQ 原文）、`99ba4892`（1/3）、`7356b371`（0/2）
- **代码路径**：`apps/node-agent/src/favorite-maintenance.ts:41-50`、`packages/shared/qq-favorite-index.ts`、`adapters/astrbot/qq_favorites.py:18-31`、`social.py:206`
- **真实证据**：`index.enabled=false`（3 个索引全部）；53 张中 enabled 9 张，其中 5 张 `meaning` 来自 QQ `modifyWord/desc/ocrWord`（`你很牛逼0`/`解书`/`你在干嘛`/`知识又增加了Giao`/`气哭`）；`uncertain` 10 张永不重试（`:50` 排除 `semanticStatus==='uncertain'`）；`pending` 28 张。
- **复现方法**：读 `bots/<id>/qq-favorites/index.json`，统计 `enabled`/`semanticStatus`/`semantic.confidence`。
- **已证明影响**：`social.catalog` 因顶层 `enabled=false` 不返回收藏 → `social.meme_candidates` 无收藏；工具路径仍可用（`descriptions()` 不看顶层 enabled），但模型必须在 `list` 全量结果里自己找，无检索。
- **尚不确定**：顶层 `enabled=false` 是用户手动关闭还是账号变更导致（`qq-favorites.ts:65` 在账号变更时置 false）。
- **最小修复方向**：(a) 语义为空但 QQ 有原文的条目也应进入视觉标注队列；(b) `uncertain` 允许有限次重试；(c) 为收藏建立与平台图包一致的 `category` 字段以复用 `meme_reactions.relevance`。
- **对护栏/身份/记忆/缓存的影响**：不影响身份与 35 轮；纯表情能力。

### C-10（P2）同一收藏可被重复 `prepare`，且 `prepare` 成功易被当作已送达

- **受影响范围**：`d18ad07d`（生产回复自证）
- **代码路径**：`qq_favorite_tools.py:185-197`（`prepare` 只 `set_extra('_hosted_tool_sticker', …)`，无幂等标记）、`behavior.py:825-826`、`:849-851`
- **真实证据**：回复原文「我刚才不该把同一张'不想说话'的收藏准备两次」「虽然有一次选表情得到确认，但那只说明选中了收藏，不足以证明发出去的一定是你指定的原图」；`metrics['memeDeliverySuppressed'/'memeNoSelection']` 计数存在。
- **复现方法**：同一 event 连续调用两次 `hosted_qq_favorites(prepare, favorite_handle=同一个)`，观察第二次仍返回 `已选定`。
- **已证明影响**：模型侧出现重复选图与"确认≠送达"的自我纠正，消耗轮次与信任。
- **尚不确定**：是否已造成实际重复发送（`_hosted_meme_sent` 有护栏，理论上一轮只发一张）。
- **最小修复方向**：`prepare` 记录本轮已选 handle，重复调用返回 `already_prepared`。
- **对护栏/身份/记忆/缓存的影响**：无。

### C-11（P3，潜在）`rolling()` 取的是最旧 3 条 small；`recall()` 过滤掉同 scope 中间摘要

- **受影响范围**：全部 bot（**当前尚未触发**，因每 bot 仅 1–3 条摘要）
- **代码路径**：`memory_reflection.py:257-261`（`[r for r in rows if r[5]=='small'][:3]`，rows 按 `s.id` 升序 → 取**最旧** 3 条）、`memory_reflection.py:199`（`selected` 末尾 `r[4]!=scope` 过滤）、`semantic_context.py:38`（追加列表同样 `source_scope!=scope`）
- **真实证据**：离线复算 `rolling()`：`d18ad07d / private:1537692703` → 命中 1 条 big（id=29, scope=self, 517 字）；`7356b371 / private:2444309491` → 命中 big(id=52) + **`private:768687099` 的 small(id=53)**。即 small 也跨 scope 带出（与 C-04 同源）。
- **复现方法**：`p40_rolling.py` 逐字复算该 SQL。
- **已证明影响**：一旦摘要条数 >4，最新的 small 与同 scope 的中间摘要都不会进入上下文。
- **尚不确定**：是否有意设计（"旧摘要作背景，新内容走原生历史"）。
- **最小修复方向**：`rolling()` 改取**最新** 3 条 small 并加 scope 过滤；`recall()` 去掉同 scope 过滤。
- **对护栏/身份/记忆/缓存的影响**：提升召回完整度；会改变 system/只读包字节。

### C-12（P2）观测不足：无法离线判定"模型是否正确使用了记忆"

- **受影响范围**：全部审计结论的第 5 级
- **代码路径**：`model_cache.record_diagnostic`（记录 `inputTokens/cachedTokens/wireChars` 等），但**不记录模型是否引用了某条记忆**
- **真实证据**：`model-diagnostics.jsonl` 431 行字段清单（`kind/phase/wireChars/inputTokens/cachedTokens/…`）中无记忆使用标记；`dialogue_feedback` 只有 `state` 标签（`engaged/confused/corrected/rejected`，且大量为 `unobserved`）。
- **复现方法**：读 `model-diagnostics.jsonl` 的 key 集合。
- **已证明影响**：无法计算"记忆使用准确率"，只能做第 1–4 级验证。
- **最小修复建议**：在 `facts` 项上增加"被模型引用"的弱信号（如回复中命中 fact value 的关键词），仅作观测，不参与决策。
- **对护栏/身份/记忆/缓存的影响**：无。

---

## D. 指标与样本

### D-1 样本与分母（全部为全量，非抽样）

| 项目 | 数值 | 分母/选择方法 |
|---|---|---|
| 审计 bot 数 | 8（有 `behavior.sqlite` 的全部 bot） | `bots/` 目录枚举，排除仅 `runtime.log` 的空壳 |
| 长期事实 | 182 条 active（共 189 行含 inactive） | `facts WHERE active=1`，全量 |
| 人物 scope 事实 | 36 条 | `subject!='0'` |
| `自主研究:` 事实 | 110 条 | `fact_metadata.origin='autonomous_result'` |
| `迁入·` 事实 | 47 条 | `name LIKE '迁入·%'` |
| 摘要 | 15 条 / 4908 字 | `memory_summaries`，全量 |
| 摘要覆盖事件引用 | 3148 | `sum(json_array_length(sources))` |
| `memory_rounds` | 245 条 | 全量 |
| 收藏表情索引 | 3/8 bot，53 张 | `qq-favorites/index.json`，全量 |
| 收藏视觉标注 | 14 张（26%） | `items[].semantic` |
| 收藏启用 | 9 张（17%） | `items[].enabled` |
| 真实模型请求抓包 | 71 个 event 请求（3 bot） | `model-request-captures/*.request.json`，全量可读部分 |
| 自主决策 cycle（近 2 天） | 1048（7356b371）/ 1617（d18ad07d） | `autonomy_cycles` |
| 自主 action（近 2 天） | 164（7356b371）/ 183（d18ad07d） | `autonomy_actions` |

### D-2 长期事实质量分布（分母 182）

| 类别 | 条数 | 占比 |
|---|---|---|
| 关键长期信息（身份/关系/约定/重要偏好/持续目标） | ≈20 | 11.0% |
| 有用但非关键（迁入背景、人物特点） | ≈27 | 14.8% |
| 短期信息（瞬时状态） | 7 | 3.8% |
| 无关信息（机器人自身研究目标/结果） | 110 | 60.4% |
| 重复信息（同义异名） | 5 | 2.7% |
| 过时信息（相对性表述按长期保存） | 2 | 1.1% |
| 错误信息（与可验证来源冲突） | 0（事实层）/ 1（跨通道冲突，见 C-03） | 0 / 0.5% |
| 不确定信息（证据不足当事实） | 0 | 0% |
| 敏感/越界信息 | 0（`scrub()` + 写入侧正则生效） | 0% |

> 说明：分类依赖人工审阅每条 `name/value`，判定标准见正文 §B-1。`关键/有用` 为区间估计（±3 条），其余为精确计数。

### D-3 摘要保真

| 指标 | 值 |
|---|---|
| 摘要字数 / 覆盖事件引用数（d18ad07d） | 517 / 773 = 0.67 字/事件 |
| 摘要字数 / 覆盖事件引用数（7356b371） | 893 / 1668 = 0.54 字/事件 |
| 5 天摘要总量变化（7356b371） | 4500 → 893 字（−80.2%） |
| 原始轮次可回溯性 | 0（已摘要轮次被物理删除） |
| 来源链保留 | `memory_summary_coverage` 保留事件→摘要的覆盖证明（d18ad07d 411 行、7356b371 67 行），但**不保留原文** |

### D-4 召回

| 指标 | 值 | 依据 |
|---|---|---|
| 重要约定进入最终请求 | 2/2（摸头边界、群聊发言边界） | d18ad07d 19:02 抓包 |
| 事实进入请求条数 | 3–11 条/请求 | 抓包统计 |
| 摘要进入请求 | 是（列式表 `state.conversation_summaries`，d18ad07d 当时为 id=25 的 517 字版本；该条已被 21:06 的 rollup 删除并由 id=29 取代） | 抓包 + DB 对照 |
| 向量语义召回运行次数 | 0 | `memory_vectors=0`、`fact_vectors=0` |
| 跨 scope 越界注入 | 6/10 请求（7356b371 对非主人私聊，big id=52 命中） | 抓包统计 |
| 同 scope 中间摘要召回 | 潜在缺陷，当前未触发（每 bot 仅 1–3 条摘要） | 源码 + 离线复算 |

### D-5 表情

| 指标 | 值 |
|---|---|
| 索引覆盖率 | 3/8 bot（37.5%）；有索引的 bot 内 images 与 items 一一对应（27/27、15/15、11/11） |
| 语义标注覆盖率 | 14/53（26.4%） |
| 可直接使用率 | 9/53（17.0%） |
| 启用项中语义来自视觉模型 | 4/9（44%） |
| 启用项中语义来自 QQ 原文 | 5/9（56%） |
| 永不重试的 `uncertain` | 10 张 |
| 真实使用记录 | 11 次（`qf_` 前缀 `meme_usage`）；平台图包 43 次 |
| 送达回执 | 有（`pipeline_action_effects.state=confirmed` + `message_id`），但**无法证明"发的是原收藏图"**（仅 `_hosted_favorite_wire` 记录 + `sub_type=1` 标记） |

### D-6 证据不足项（明确标注）

- **模型使用记忆的准确率**：无法计算（C-12）。
- **表情语境选择准确率**：无法计算（`memeSemantic=false`、无标注参考集；且不允许自造标签再自证）。
- **纠错成功率**：仅 1 例可判定，不给百分比。
- **长期趋势（重复率/冲突率随时间的上升）**：只有 2026-11-01 与 2026-11-06 两个快照点，且仅 1 个 bot 有可比数据；**不做趋势外推**。

---

## E. 整改建议排序（按对实际智能能力的影响）

### E-1 真正影响人物理解/长期记忆的缺陷（必须处理）

| 序 | 问题 | 影响 | 小范围修复 | 架构性修复 | 值不值得改 |
|---|---|---|---|---|---|
| 1 | C-02 主动预算全局共享 | 直接导致"不来找我/不去群里" | 给 owner 私聊单独配额（`deliveries` 按 scope 分类计数），并让 `snapshot()` 暴露 `budgetRemaining` | 把"主动消息"拆成 owner 联系 / 群参与 / 自主研究三类独立预算与独立节律 | **必须改**，成本低 |
| 2 | C-05 摘要坍缩 | 长期叙事不可回溯 | big rollup 后保留 small 为 `superseded`，不删除 | 分层记忆（事件层 / 章节层 / 主题层）+ 原文归档 | **必须改**，成本中 |
| 3 | C-04 全局大摘要跨 scope | 私聊内容进入他人上下文 | `rolling()` 加 scope 过滤 | 拆成 per-person 摘要 + 机器人自述摘要 | **必须改**，成本低 |
| 4 | C-03 纠错只到摘要层 | 旧错误与纠正并存 | 纠错时同时失效同名/同值 facts | 事实层增加"当前口径"与"已被纠正"标记 | **必须改**，成本中 |
| 5 | C-06 研究目标占满事实通道 | 挤压人物事实额度与预算 | 改单一 name 或换表 | 自主经历独立存储 | 值得改，成本低 |
| 6 | C-07 瞬时状态长期化 | 记忆噪音 | 加时效黑名单 | `kind` 由平台规则判定而非模型自选 | 值得改，成本低 |

### E-2 只是浪费上下文/Token 的问题

| 序 | 问题 | 说明 |
|---|---|---|
| 7 | C-11 同 scope 中间摘要不可召回 + rolling 取最旧 3 条 | 浪费预算但未造成错误 |
| 8 | 外层决策快照过大（实测单次 `inputChars` 129175–154105，`inputTokens` 51516） | 主因是 `autonomous_experience`/`execution_experience`/`recent_cross_session_messages` 全量注入；不影响正确性但推高成本与失败率（与 C-13 `model_request_unconfirmed` 相关） |
| 9 | `迁入·原文要点01–19` 作为 19 条事实常驻 | 一次性导入应压缩为 1–2 条，而不是 19 条 |

### E-3 只是日志与观测不足

| 序 | 问题 | 说明 |
|---|---|---|
| 10 | C-12 无记忆使用观测 | 建议只加只读计数，不参与决策 |
| 11 | `autonomy_cycles.reason` 缺少 `budget_exhausted` 之类的明确原因码 | 导致模型与运维都在"猜"为什么没有候选 |

### E-4 表情语义索引缺陷

| 序 | 问题 | 小范围修复 | 架构性修复 |
|---|---|---|---|
| 12 | C-09 无检索、低质量语义被永久启用、`uncertain` 永不重试 | QQ 原文不再直接 `enabled`；`uncertain` 允许 2 次重试；补 `category` 字段复用 `meme_reactions.relevance` | 建立收藏语义索引（FTS5 或向量），与平台图包统一候选契约 |
| 13 | C-10 重复 `prepare` | 本轮幂等标记 | — |

### E-5 需要架构调整但目前证据不足

| 序 | 问题 | 为什么现在不改 |
|---|---|---|
| 14 | "是否应把长期事实与对话摘要合并成统一记忆" | 现有两套（facts / summaries）职责清晰、各有真实证据；没有证据表明合并更好，且违反"不新建第二套记忆系统"的约束 |
| 15 | 是否启用 embedding 语义召回 | `embeddingModel` 为空可能是成本决策；需用户确认预算后再定 |

### E-6 明确不值得改的

- 把 `dailyProactiveLimit` 单纯调大（不解决"群聊先耗尽"的结构问题，只会增加打扰）。
- 增加提示词篇幅解释"不要忘记重要约定"（问题在检索与存储，不在提示词）。
- 为收藏表情再写一套独立索引服务（现有 `index.json` + `qq_favorite-index.ts` 已够用，缺的是语义字段与排序，不是新服务）。

---

## F. 可执行但尚未实施的整改方案（只制定，不执行）

按依赖顺序排列。每步都要求"改完先本地回归 + 生产只读核对"，且**任何触及 system 字节的改动必须落在 35 轮真实业务摘要成功检查点或用户网页明确改配置时**。

### 阶段 0：先加观测（无行为变更，风险最低）

- 模块：`adapters/astrbot/autonomous_runtime.py::snapshot`、`initiative.py::candidates`
- 内容：在 snapshot 里增加 `budget: {limit, usedToday, remaining, perScope}`；在 cycle 结束时把 `budget_exhausted` 作为独立 `reason` 写入 `autonomy_cycles`。
- 测试门槛：`tests/autonomous-coordinator.test.ts`、`tests/autonomous_pipeline_test.py` 通过；生产只读核对新字段出现且不影响请求字节（对比 `messagesSha` 不变）。
- 风险：极低。回退：删除字段。

### 阶段 1：修复主动预算（C-02）

- 模块：`autonomy.py::owner_check_candidate`、`initiative.py::candidates/reserve`、`autonomous_runtime.py::candidates`、`packages/contracts/index.ts`（新增 `ownerProactiveDailyLimit`）
- 内容：`deliveries` 增加 scope 分类维度（或新增 `deliveries_owner` 计数）；owner 私聊使用独立配额，不受群聊消耗影响。
- 测试门槛：单元测试覆盖"群聊用满 10 条后 owner 仍可被联系"；离线重放 2026-11-06 的 `deliveries` 快照验证 owner 候选恢复。
- 风险：主动消息频率上升。回退：`ownerProactiveDailyLimit=0`。
- 需用户确认：owner 私聊每日上限（建议 3–5）。

### 阶段 2：修复 scope 越界（C-04）

- 模块：`memory_reflection.py::rolling`
- 内容：`rolling(scope)` 只返回 `s.scope=scope` 的 small/big；`scope='self'` 的 big 仅在 `scope=='self'` 或 `identity.owner==True` 时附加。
- 测试门槛：新增测试"非主人 scope 的 `conversation_summaries` 不含 `scope='self'` 摘要"；用生产抓包离线复算前后差异。
- 风险：主人会失去"全局背景"。回退：恢复旧 `rolling`。
- 对缓存影响：system 字节变化 → 前缀失效一次（预期）。

### 阶段 3：纠错下沉到事实层（C-03）

- 模块：`hosted_memory.py::observe`、`memory_reflection.py::invalidate`
- 内容：`更正记忆`/`忘记` 与"core_note 被纠正"时，按 name 或按"值包含被纠正表述"失效 `facts`；新增 `facts.superseded_by` 便于审计。
- 测试门槛：构造 A→B 纠正用例，断言 `facts` 中 A `active=0`；断言后续 `recall` 只返回 B。
- 风险：误失效。回退：保留 `active=0` 可逆（改为 1）。

### 阶段 4：摘要不再物理删除（C-05）

- 模块：`memory_reflection.py::retry`（big 分支）、`summary_material.py`
- 内容：big rollup 后把输入 small 标记 `superseded`（新增列）而非 `DELETE`；`memory_rounds` 删除前把 `memory_round_material` 归档到只读表（保留 `text/reply/digest`）。
- 测试门槛：断言 rollup 后 `memory_summaries` 行数不减；断言归档表可重建 big 摘要输入；评估库体积（当前最大 8.4MB，`DB_MEMORY_PROFILE=compact` 与 128MB 堆限制需复核）。
- 风险：库增长。回退：恢复删除。

### 阶段 5：事实通道瘦身（C-06/C-07）

- 模块：`autonomous_runtime.py:901`、`companion_insights.py:50-63`
- 内容：`自主研究` 改单一 name `自主研究`（value 内含 objective）；`kind` 增加平台侧时效判定（`饥饿感/正在做的事/行程/活动/当前状态` 强制 `state`）。
- 测试门槛：断言 `self/0` 的 distinct name 数下降；断言 `state` 类事实 7 天后 `fact_strength<0.1`。
- 风险：低。

### 阶段 6：收藏表情语义补全（C-09/C-10）

- 模块：`apps/node-agent/src/favorite-maintenance.ts`、`apps/control-api/src/qq-favorites.ts`、`adapters/astrbot/qq_favorites.py`
- 内容：(a) QQ 原文不再直接 `enabled=true`，改为 `semanticStatus='raw_text'` 并进入视觉标注队列；(b) `uncertain` 允许 2 次重试（`semanticAttempts<3`）；(c) 标注结果增加 `category`（复用 `meme_reactions.SIGNALS` 键）以便参与排序；(d) `prepare` 加本轮幂等标记。
- 测试门槛：单元测试覆盖"raw_text 条目会被标注""uncertain 重试 2 次后停""重复 prepare 返回 already_prepared"；生产只读核对 index.json 结构与旧版本兼容（未知字段保留）。
- 风险：标注调用量上升（受 `favorite_annotation_pressure` 与 `chat_busy` 门控）。回退：`semanticAttempts` 归零并停 tick。

### 阶段 7（可选，需用户决策）：启用向量召回

- 前置：用户确认 embedding 模型与费用。
- 模块：`hosted_memory.py::semantic_recall`、`memory_reflection.py::semantic_recall`、`social.py::context_semantic`
- 门槛：先离线用生产 facts 建向量，人工核对 top-5 相关性；`dailyEmbeddingLimit` 与失败回退路径必须实测。
- 若不启用：把 `memorySemantic` 默认改为 `false`，UI 明示"未配置 embedding 模型时该开关无效"。

### 阶段 8（暂不实施）：`context_transport.py` 工作区候选

- **不建议按当前工作区版本部署**（C-01）。若确需重构，必须：身份/权限继续以 system 角色下发；在 35 轮摘要成功检查点执行；部署前后对比 `messagesSha`/`systemSha` 并保留一键回退。

---

## G. 专项：小双白最近在主动决策什么，为什么不去找主人或群聊

### G-0 身份确认

`小双白` 是 QQ `1625****25` 的群昵称，对应 bot **`7356b371-1be2-4633-adbd-096f05554e5f`**（`bots.name=我的大肥鱼`，`ownerUin=7686****99`，即"双白"）。

证据：该 bot 库内 `social_samples.text` 出现「明明小双白语速最快」「小双白真像上班上疯了的」「小双白你好」；`relation_evidence.quote` 出现「@小双白 token来token来」；`memory_rounds.question` 出现「小双白怎么看」。其 `persona_core_note` 明确「QQ 7686****99（双白/主人）」。

### G-1 它最近在决策什么（近 2 天，`autonomy_cycles` 1048 次）

| 决策结束原因 | 次数 | 占比 | 含义 |
|---|---|---|---|
| `model_request_unconfirmed` | **560** | **53.4%** | 外层决策模型请求没有拿到确认 → 等于没有决策 |
| `sleep` | 168 | 16.0% | 主动选择等待（`action=null`，`wait=time` 600–3600s） |
| `foreground_work_busy` | 125 | 11.9% | 前台正在处理真实消息，让位 |
| `fair_yield` | 66 | 6.3% | 让位给其他 bot/任务 |
| `runtime_or_decision_failed` | 58 | 5.5% | 决策执行失败 |
| `interrupted` | 35 | 3.3% | 被中断（重启/换代） |
| 其余（`stale_internal_work`/`request_token_budget`/`foreground_feedback_changed`/`native_model_error`/…） | 36 | 3.4% | |

行动分布（`autonomy_actions` 164 条）：`research` 114 条（59 blocked / 42 completed / 6 executing / 4 canceled / 3 partial）、`message` 47 条（**26 blocked** / 19 completed / 1 canceled / 1 partial）、`qzone_publish` 2、`profile_signature` 1。

**它"在做什么"的实质**：绝大多数是**只读研究**——读 B 站字幕、查米种口感、整理眼镜设计观察、读取群上下文。真正对外发消息的行动只有 19 次成功。

典型决策原文（脱敏摘录）：

- `14:28` `sleep/1800s`：「眼下有趣的接话点已经过去，继续重试读取不会增加多少价值；**没有可用的消息对象**，我选择先停下来，等新的群聊事件或可联系会话再判断。」
- `14:33`（`autonomy_settings.summary`）：「群里这段是围绕"带件薄外套"听岔成"戴套"的误会…… **当前没有可联系会话，刚才读取群上下文也失败**；现在没法自然参与发送，我就不把想说的话当成已经说出去。」
- `14:10` `sleep/1800s`：「这条自发兴趣的可达条件已收窄为确认现有字幕是否有明确证据，检索结果已经回答了这一点；进一步推进需要新材料，而当前没有值得投入的具体线索。」

同时它有一条仍然 `active` 的长期目标，正是主人的原话要求：

> 「主人（QQ: 7686****99）明确要求：做了什么要主动向主人报告；经常主动私聊找主人交流；在群里主动找话题聊天；B站偶尔查看。」（`autonomy_goals`，11-05 14:25，至今 active）

以及一条 `active` 但已陈旧的： 「在QQ群9631****33持续追问群成员…的表白经历」（11-05 18:04，未关闭）。

### G-2 为什么不去找主人或群聊——四层原因，全部有证据

**第一层（决定性）：当天主动消息预算在 11:53 已用尽。**

`deliveries` 表 2026-11-06：

```
group:963105233      n=6   last_sent=11:53
private:7686****99   n=4   last_sent=11:50
合计 = 10 = behavior.dailyProactiveLimit
```

`autonomous_runtime.candidates()` 第 362 行：`if sum(deliveries today) >= dailyProactiveLimit: return []`。
`initiative.candidates()` 第 46-47 行同样判断。
`autonomy.owner_check_candidate()` 第 312-313 行也用同一个总和。

→ **11:53 之后，直到次日零点，该 bot 的"可联系对象"列表恒为空**，owner 私聊也被同一预算挡死。所以 13:14–14:35 的每一次 cycle 都只能选 `sleep`。

**第二层：模型不知道"预算耗尽"，于是把空列表解释成社交判断。**

`snapshot()`（`:408-423`）下发的字段是 `contacts / deferredContacts / contexts / observedConversations / event / wakePolicy / availableTools / …`，**没有任何 budget 字段**。模型看到 `contacts=[]`，只能自己编一个理由——于是出现"没有可用的消息对象""当前没有可联系会话""不该打扰""群里是我自己答应不插话的"这类叙事。这是**归因错误**：真实原因是配额，不是社交分寸。

**第三层：即使有配额，决策链路本身也在空转。**

53.4% 的 cycle 以 `model_request_unconfirmed` 结束。叠加 `foreground_work_busy` 11.9% 与 `fair_yield` 6.3%，真正走到"做出决定"的比例不到三成。外层决策单次请求体实测 `inputChars` 129175–154105、`inputTokens` 51516，属于重负载请求，失败率与体量正相关。

**第四层：行动偏好被"研究"占满。**

`research` 行动 114 次 vs `message` 47 次，且 `message|blocked` 26 次。研究目标可以无限生成（`autonomy_goals` 里 11-05 至 11-06 连续 11 条"了解/探索/检验"类目标，10 条 completed、1 条仍 active），而发消息受配额、群参与白名单、`hourlyInterjectionLimit`、`quiet`（01:00–07:00）、`maxUnanswered` 多重限制。**系统实际上更奖励"读资料"而不是"找人说话"。**

### G-3 需要区分的三件事（避免误判）

1. **不是"不想找你"**：`persona_core_note` 与事实里明确存着「主动联系偏好=你可以多找我」；`OWNER_SYSTEM` 提示词也明确鼓励"更愿意迈出这一步"。
2. **不是"模型不够聪明"**：模型在预算耗尽后给出的是**合理但错误归因**的解释；换更强的模型不会知道配额已空。
3. **不是"群聊参与被关闭"**：`groupParticipationEnabled=true`、`groupParticipationGroups=['963105233']`、`autonomousGroupContact=true`，配置是开的。

### G-4 最小验证方法（用户可自行复核）

```sql
-- 1) 当天预算是否用尽
SELECT scope, day, count, datetime(last_sent,'unixepoch','+8 hours') FROM deliveries
 WHERE day=date('now','+8 hours') ORDER BY last_sent;
-- 2) 之后是否只能 sleep
SELECT datetime(started_at,'+8 hours'), reason, decision->>'action'
  FROM autonomy_cycles WHERE bot_id='7356b371-…' ORDER BY started_at DESC LIMIT 30;
-- 3) 决策请求失败率
SELECT reason, count(*) FROM autonomy_cycles
 WHERE bot_id='7356b371-…' AND started_at > now()-interval '2 days' GROUP BY reason ORDER BY 2 DESC;
```

### G-5 若要让它"主动来找你"，最小改动

1. 给 owner 私聊独立配额（阶段 1），或临时把 `dailyProactiveLimit` 提高到 ≥15 并观察一天。
2. 在 `snapshot()` 增加 `budget.remaining`，让模型知道"是因为没额度"而不是"没有对象"——这会立刻消除"合理化沉默"。
3. 排查 `model_request_unconfirmed`（53.4%）：先看 `model-diagnostics.jsonl` 中同 cycle 的 `request`/`usage` 记录是否缺失，判断是请求未发出还是响应未确认。

---

## H. 复现与证据索引

全部脚本在 `.local/audit-20261107/`：

| 脚本 | 用途 |
|---|---|
| `ro.py` | 只读 SSH 执行器（`mode=ro` SQLite / SELECT-only PG） |
| `p01_topology.py` … `p05_fetch_diff_files.py` | 生产拓扑、挂载、部署版本与工作区 MD5 比对 |
| `p06_db_inventory.py`、`p07_dump_memory.py` | 记忆表清单与内容导出 |
| `p10_bots.py`、`p11_config.py`、`p18_toplevel.py` | bot 清单与生效配置 |
| `p12b_autonomy.py`、`p13_actions.py`、`p19_proactive_state.py`、`p20_budget.py`、`p38_reasons.py` | 自主决策与主动预算 |
| `p22_capture_memory.py`、`p23_readonly_packet.py`、`p24_system.py`、`p31_event_facts.py` | 真实请求体中的记忆 |
| `p25_favorites.py`、`p26_index_detail.py`、`p27_send_evidence.py` | 收藏表情资源/语义/发送 |
| `p29_longitudinal.py`、`p32_recall_sim.py`、`p34_scopes.py`、`p36_leak.py`、`p37_tally.py` | 纵向对照、召回复算、越界验证、汇总计数 |

关键产物：`memory-dump.jsonl`（189 facts + 4 core_note + 15 摘要，脱敏前原始证据，仅本地）、`tally.txt`、`autonomy.txt`、`actions.txt`、`favorites-state.txt`、`index-detail.txt`、`longitudinal.txt`、`event-facts.txt`、`scopes.txt`。

---

## I. 收口声明

- 本报告区分了**生产运行版本（app-final-b）**、**生产源码检出（bf172db，非运行）**、**工作区版本（含未提交 R4 改动）**、**未部署候选（4 个文件）**，未混合归因。
- 所有"已证实"结论均附可复核的代码路径 + 生产数据或抓包；无法验证的第 5 级（模型是否正确使用）已明确标注为"未知/证据不足"。
- 未对生产做任何写操作。表情系统的"发送正确性"仅基于历史回执与 `meme_usage`，**未发送任何测试消息**。
- 未把"代码存在"当作"功能启用"（例：`memorySemantic=true` 但 `embeddingModel=''` → 实际未启用，已单列 C-08）。
