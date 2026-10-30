# 社交学习与表情包时机：源码调查和接入决定

调查日期：2026-10-28。项目位置：`D:\MYCODE\APP\fishbot\qq-ai-saas`。

本次问题以用户后续澄清为准：“学习能力”指学习别人说话、用图习惯及群聊氛围；还要研究何时发图。人物事实记忆不能代替这些能力。

## 结论及当前产品边界

确有项目实现了相关机制，不需要只靠一句“像真人聊天”的 Prompt。可分成：表达学习、黑话理解、接话决策、图片收录和理解、配图决策、实际投递六层。它们不是同一个开关，也不是模型权重训练。

现有产品的事实记忆、群聊触发、主动消息和 QQ 内置表情机制，不能证明已经实现了群体表达学习或自适应表情包使用。本轮没有把新插件装入真实共享运行组，没有改变真实账号、Key、数据库或运行配置。图片 URL 发送工具存在，不代表已经验收“自动学图并选准时机”；当前视觉 API 的历史 502 也尚未解决。

推荐保留 AstrBot。先复用 AstrBot 生态的实际插件机制，在独立测试实例验收，再处理共享模式的 Bot 作用域。MaiBot 值得借鉴学习、筛选和规划方式，但本轮没有据此切换核心。

## 固定源码证据

所有新快照位于 `references/social-learning/`；提交和工作区洁净状态由探针保存于 `test-results/social-learning/upstreams.json`，不是根据搜索摘要推测版本。

| 项目 | 本次提交 | 版本依据 | 主要价值 |
|---|---|---|---|
| [MaiBot](https://github.com/Mai-with-u/MaiBot) | `6bcb2e068cd98793a132cb8cc4ebf3cc00cb8fc3` | README 标示 1.3.10 | 情境—表达学习、候选筛选、图像候选选择 |
| [Self Learning](https://github.com/NickCharlie/astrbot_plugin_self_learning) | `cef208d24d0d5cbf25356320508406f98ae6997d` | metadata 4.4.3，AstrBot >=4.11.4 | 表达模式、黑话统计、特定人物表达习惯 |
| [Group Chat Plus](https://github.com/Him666233/astrbot_plugin_group_chat_plus) | `aa97e0e35a18733e73db753238dd2a3021446e42` | metadata V1.2.3.hotfix.2，AstrBot >=4.11.0 | 群聊接话决策、疲劳、回复密度 |
| [Meme Manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) | `d0843c16eac11b19d1ca64a394749ee13fc93805` | metadata 5.2.9，AstrBot >=4.17.0 | 分类表情库、主模型选情绪标记、输出转换 |
| [Meme Magpie](https://github.com/Whereis-Alice/astrbot_plugin_meme_magpie) | `06c8e26de3a11da8a63fc497d4f3d03a4cb67f4f` | metadata 1.12.5，AstrBot >=4.24.1 | 自动收录、语义检索、候选拒绝、发送限流 |

MaiBot 旧地址 `MaiM-with-u/MaiBot` 现重定向到 `Mai-with-u/MaiBot`。搜索结果中旧版本号不能作为本次源码版本。这些项目没有被证实都是 B 站某一个“大肥鱼”作者的同一实现。

## 1. 学习别人怎么说话

### MaiBot：保存“情境—表达”，不是照搬所有聊天记录

入口 `maibot/src/learners/expression_learner.py`：`collect_context_messages`、`_learn_from_session_messages`、`_run_learning_batch`。源码给聊天消息建立来源标识，调用独立 learner 模型提炼表达，再筛选并写入。一次提取超过 20 条时丢弃该批，存在学习并发 gate 和入库检查；不能把任何一句原话不加检查就当习惯。

回复侧 `src/chat/replyer/maisaka_expression_selector.py:MaisakaExpressionSelector` 按会话的 expression_groups、审核状态等加载候选，再筛选；`MAX_SELECTED_EXPRESSIONS=5`。部分选择路径使用子代理或向量索引，意味着额外成本，不是免费能力。

两个可直接借鉴的细节：

- `src/learners/expression_style_utils.py` 去掉提取残留的“使用”前缀，并排除 Prompt 自己给出的示例表达，避免模型反复学自己的示例。
- `src/learners/weighted_sampling.py` 将频次权重限制在 1–5，且不放回抽样；高频口头禅不会仅因频次高而无限垄断候选。最终语境匹配仍需其他模块完成。

### Self Learning：表达模式、黑话、模仿习惯分别实现

- `services/analysis/expression_pattern_learner.py:ExpressionPattern` 有 situation、expression、weight、时间、group_id、persona_id、user_id；学习器设上限 300、最少 5 条消息、最短间隔 300 秒。这些是该模块常量，不代表插件所有学习路径都遵守同一上限。
- `services/jargon/jargon_statistical_filter.py` 用群内频次、跨群分布、突发程度、发言者分布和上下文筛候选，再交给模型。源码里的成本下降百分比是作者说明，本轮没有测量该收益。
- `services/shadow_mode.py:ShadowModeService` 提取平均长度、标点、结尾等，`build_prompt` 将这些统计注入回复。它更接近“学某个人的说话习惯”，不是身份事实记忆。
- `services/hooks/llm_hook_handler.py` 在请求时取表达/黑话/影子模式上下文，优先附加上下文而不是无限改写整份固定人格。

**共享部署阻碍已有具体证据**：`services/database/facades/learning_facade.py:get_approved_few_shots` 约第 690 行，SQL 仅以 approved + group_id 过滤；调用方 `_fetch_few_shots(group_id)` 没有 Bot 参数。同一共享插件数据库里，两台 QQ 进入同一群时，存在选中同一份学习材料的可能。另有 group_id→UMO 映射及单例学习器。部分表达表有 persona_id，并不证明全部路径已经按 Bot 隔离。独立实例数据库下的问题与共享进程下的问题必须区分。

## 2. “读空气”：先决定是否接话

Group Chat Plus 的 `main.py` 约第 6506 行调用 `utils/decision_ai.py:DecisionAI.should_reply`。输入不只是当前一句话，还包括上下文、兴趣、人格、连续回复疲劳、回复密度、冷却提示、主动发言标志等。概率预筛后仍可让判断模型决定不回复；决定回复后再进入回复生成。

这比“群里每句话都问模型并回答”自然，也比固定随机插话更了解语境。但单独判断模型会增加请求数、延迟和 Token，必须配置触发预算。其 attention/density 状态的 key 使用 platform_name/chat_id 等，仍需审计平台名是否在我们的共享实例里真正包含唯一 Bot 身份，不能只因字段名存在就判定隔离。

可借鉴规则：私聊、明确 @、相关话题、不相关聊天、多人讨论、连续抢话分别处理；沉默是合法结果。人物记忆召回应尽量放在决定要参与之后，避免无关群消息也进行重检索。

## 3. 学表情包与何时发图

### Meme Manager：较低门槛的第一条路线

主模型按已知分类输出情绪标记；`mixins/event_handlers.py` 负责解析标记、流式过滤、选图、转换成 Image。`_build_emotion_images_for_event` 约第 615 行先做概率门，再从当前事件绑定的分类表情库选文件。因此“情境选分类”主要交给主模型，“分类内哪一张”在该路径为随机选择。概率只是限频，不能代替语境判断。

可用预先审核、带分类的图片包，不强依赖常驻本地 Embedding 或每次额外视觉调用。可选的 emotion LLM 和语义模式另算成本。README 要求安装图包后保存会话/人格使用绑定；只下载图包不会自动生效。

`backend/auto_collect.py` 是收录管线，和回复时选图是两条职责。自动收录和图像理解不是因为启用发送就一起完成。

### Meme Magpie：更细的场景匹配与明确“不发”

- `core/events/event_handler.py` / `background_steal_queue.py`：接收图片、收录开关、队列、冷却和容量控制。
- `core/events/meme_sender_engine.py:should_skip_auto_emoji_by_gate`：短文本、命令、部分严肃关键词和多问号会跳过；默认同会话 20 秒自动发图冷却。`_MemeTurnState` 保证同回合自动发送只 claim 一次。
- `core/search/candidate_reranker.py:MemeCandidateReranker`：从有限候选 ID 选择或返回 none，提示模型检查发送者/接收者、图中文字、动作、角色，避免“安慰配成嘲笑”。**明确不发与模型故障是不同返回值**，不能把 none 当作失败再随机补图。当前实现还会在发送前重新检查图片 hash、存在性和作用域。
- `core/events/emoji_delivery.py:send_qq_image_as_sticker`：通过 QQ OneBot 的 summary、sub_type/subType 等字段尝试按表情样式投递，失败回退普通图片。这是协议实现相关优化，不是所有 OneBot 的通用保证，本轮未在真实 QQ 验收这些字段。

其关键词 gate 仍有局限：实际函数会阻止“哈哈这波失败也太搞笑了”，却不会仅凭文字阻止“我家人去世了，今天很难过”。因此必须保留语义判断和人工样例，不能把关键词表当“懂氛围”。

### MaiBot 与 QQ-agent：选择图的另一种组织

MaiBot 的 `src/maisaka/builtin_tool/send_emoji.py` 组织候选图网格，让子代理选择图；`src/maisaka/context/emoji_candidates.py` 保存候选快照，避免历史候选随库变化而失去对应关系。该路径需要视觉任务配置，不能用当前视觉失败的服务假装已可用。

已有参考快照 `qq-ai-saas-research/sources/K0nd1us__QQ-agent/src/stickers.js`，提交 `789c7f75d169fa851294de20706b6be8ca51552a`：`buildStickerContext` 用一半常用、一半按时段稳定轮换的候选，避免整库进 Prompt；`buildStickerStrategyHint` 对玩笑、安慰、尴尬、接表情等场景给模型建议。其“每几轮一图”是 Prompt 倾向，不能当作程序级频率上限。没有据此声称该项目已经实现持久化语言习惯学习。

## 接入决定：复用机制，但统一发送和租户边界

第一步适合在 dedicated 测试实例接入 Meme Manager 的“审核图包→分类标记→Image”链路，验证普通图、动图、纯图、文字配图和流式标记不泄漏。先解决可控配图，再添加自动收录；当前视觉接口失败时，未知图片只进待审，不编造标签。

第二步复用 Self Learning 的情境表达、黑话和统计习惯机制，与现有事实记忆分开。先保存候选及来源，审核/阈值后使用；回复时只注入少量相关表达，不允许学习结果改写主人身份、权限或工具规则。可配置学习范围、强度、回滚和关闭。

第三步引入 Group Chat Plus 的接话判断思路。只让一个模块拥有是否生成回复的决定权，避免原有主动发言和插件定时器各发一次。主模型若已能选择配图，不默认叠加第二个情绪模型、第三个精排模型；精排作为可选增强，记录实际调用成本后启用。

共享运行组接入前至少补齐这些作用域：

```text
表达/黑话：tenant_id + bot_id + conversation_id + persona_revision
图片来源：tenant_id + bot_id + origin_conversation + content_hash
使用记录：bot_id + conversation_id + image_id + sent_at
待发动作：bot_id + generation + event_id + config_revision
```

原图和学习材料默认在所属 Bot 内可见；跨群/跨 Bot 共享必须明确配置。可共用只读审核公共图包，不能把客户收录库默认当公共库。直接复制这些字段还不等于隔离：查询、缓存、定时任务、工具参数和图片下载权限都需要同一边界。

两个独立决策必须落到程序：**要不要参与聊天**，以及**这次要不要用图/用哪张**。允许仅文字、仅表情、图文和沉默；没有合适图就不发。群里认真讨论故障、难过或争执时，不因单一“生气/开心”标签自动配梗图。主动任务也要重新核对安静时段、冷却、额度、配置版本及运行状态。

## 本轮可复现验证

执行：

```powershell
cd D:\MYCODE\APP\fishbot\qq-ai-saas
references/astrbot/.venv/Scripts/python.exe experiments/social-learning/probe.py
```

10 项离线测试通过，日志 `test-results/social-learning/probe.log`；源码 hash、实际关键词 gate 结果和测试范围在 `probe.json`。测试直接导入 MaiBot 纯函数，并从原始 AST 提取 Magpie gate 和回合状态类，未改写函数逻辑；没有启动完整插件。

覆盖：表达权重上限、不放回采样、Prompt 示例污染过滤、命令/短句/关键词 gate、禁用 gate、单回合一次发送及新回合重置。两个语境误判样例保存为限制证据，不伪装成语义能力通过。

未执行：新插件完整 AstrBot 集成、真实图像识别、真实 QQ 自动收图/选图、模型费用/延迟对比、群体风格学习效果、多 Bot 插件状态隔离。因此不宣称这些能力已经上线，也没有重新引用旧 32 项通过作为本轮插件验收。

## Plan vs Actual

| 本次澄清要求 | 实际交付 | 状态 |
|---|---|---|
| 找到会学别人说话的真实实现 | MaiBot learner/selector、Self Learning 表达/黑话/影子习惯源码 | 完成源码调查；学习质量未实测 |
| 找到会学表情包的真实实现 | Magpie 收录/识别/检索/发送；Manager 收录与分类链路 | 完成源码调查；未接入生产 |
| 研究发图时机 | 上下文判断、概率、冷却、一次发送、none、语义错配分析 | 完成；离线 gate/状态测试通过 |
| 可复用且不破坏共享隔离 | 发现 group-only 查询等阻碍，给出独立实验优先及作用域要求 | 方案明确；改造和共享验收未完成 |
| 既有 SaaS/真实 QQ 保持可用 | 本轮只新增独立快照、探针和本文件，不修改生产路径 | 无新增生产行为 |

本轮范围是用户澄清后的针对性源码研究。没有把先前第三轮大计划中的所有待办宣布完成，也没有把下载插件或纯函数通过当作正式功能已经启用。
