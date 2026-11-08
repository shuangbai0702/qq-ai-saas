# FishBot 公共能力合同（2026-11-08）

本合同收敛当前实现，不另建工具框架、消息历史、记忆库或规划 AI。业务授权、持久化账本与恢复仍归原模块。

## 1. 工具声明与结果

- 工具仍使用原生 `FunctionTool`；B 站继续由 Bridge 的声明、参数校验与效果账本负责。QQ 文本长度由 `qq_actions.CONTENT_LIMITS` 同源生成 Schema 和执行限制；自动签名另受现有 40 字约束。
- `hosted_effect` 声明是否可能产生外部写操作；混合工具使用 `hosted_write_operations` 声明具体写操作。执行诊断通过 `tool_outcome.writes(tool, arguments)` 判断本次调用，不能只看工具名。
- `hosted_result_detail` 只允许受控操作状态进入内部结果摘要，不能用于任意读取正文。它不授予执行权限。
- `structured()` 是幂等包装，前台功能工具、后台功能工具和内部基础工具复用 `project()` 的结果语义。不得再根据“发送成功”等自然语言推断写成功。

| 维度 | 值 | 合同 |
|---|---|---|
| execution | completed / failed / canceled / pending | 计算或登记是否完成，不证明外部效果 |
| receipt.status | confirmed / rejected / partial / unknown / not_applicable | 外部效果证据；读取和本地登记通常为 not_applicable |
| code | 受控错误码 | 拒绝、不可用及异常应可定位，异常不得泄露请求正文或凭据 |
| evidence | 原账本或协议证据 | 保留原来源；不能用模型声明代替回执 |

`queued=false`、`ok=false`、`available=false` 是失败；`queued=true,state=pending` 仍为 pending。MCP `CallToolResult` 保留图片、音频、资源等原生块，文本使用同一结果封装，同时提供 `structuredContent`。MCP 错误可与已确认回执并存，不能因后续处理失败抹掉 confirmed/partial 证据。

未知写结果不得盲目重试。重复业务请求返回原账本状态，不重新发送。取消、`WorkStopped` 和预算终止必须传播给所属 Work，不能变成普通工具失败后继续调用模型。

## 2. 模型响应与公开表达

新 Provider 响应和 SDK 模型响应入口复用 `sanitize_new_response()`。只处理明确的 think/thinking 控制块，包括事故中的未闭合变体；代码及引用标签示例保留，不按普通语言内容猜测推理。

SDK 公开响应通道排除 `chain.type=reasoning`。Provider 的私有 reasoning/signature 字段可按协议参与续轮，不转成 QQ 正文或人物事实。带工具调用的中间响应不公开其正文或媒体，工具参数和调用 ID 保留；正常终局回答仍走原 OutputPlan。流式响应等待完整终包，分裂控制标记不能先行外发；缺终包是真实失败。取消或关闭消费方必须关闭所属 Provider/SDK 异步生成器。

`prepare_history()` 只深拷贝。已提交 wire 的唯一权威仍是 Journal，不清洗、删除或重排旧前缀。新摘要模型输入可以在副本中排除机器人明确推理块；原始来源、证据引用、coverage、历史字节和人类表达保持原样。

## 3. 主动行为的作者与受众

合成主动事件的协议收件人不是发言作者。资料包通过 `message_source=platform_initiative`、`recipient_id`、`recipient_is_owner`、`is_user_statement=false` 区分两者；不改变原协议事件和工具权限。

`topic_origin.kind=model_selected_topic` 只证明机器人规划了此话题，不证明用户曾经提出。自身 `autonomous_choice` 兴趣标注 `bot_self_interest_not_user_statement`；与用户真实共同话题仍可自然衔接，但须有其来源支持。禁止用“你说过”等关键词替换、机械改写或额外审核模型代替语义理解。

## 4. 聊天结算与辅助判断

Scope、Work、Freshness、OutputPlan 和原回执账本职责不变。Freshness 的 keep 保留已有覆盖并合并新声明；replace 必须声明新草稿自己的覆盖；drop 不授权事件结算。无变化的 keep 不能清空旧声明。只有整个 Work 的完整发送确认后，声明才能使后续 Work 跳过已回答事件。

程序疲劳窗口继续约束自然跟聊时长；明确请求仍有模型判断机会。冷进入的 Presence 判断通过不再自动绕过原有非定向参与额度。不增加跨 Scope 全局限流、文本近似去重或新重试队列。

辅助判断继续复用 `decision_request`、`decision_memory`、`decision_version` 与受众过滤，不共享私人 SDK 历史。稳定人格、System 和 Journal 的现有独立保护不变。

## 5. 兼容配置与发布

- `followupReplyProbability` 是历史兼容名称，目前只区分 0/正值，即跟聊关闭/开启，并非抽样概率。
- `randomReplyProbability` 是无运行消费者的旧兼容字段，不是有效调参入口；不为了旧名称重新增加随机回复。当前网页无该字段的专门控制项。
- Schema 的有效来源为当前 contracts 与当前 canonical maintenance 构建。旧发布目录存在另一份 bundle 不等于进程正在执行它，必须核验实际闭包行为与加载版本。

新增工具优先补充原工厂的业务声明、参数限制和真实回执，接入既有装配入口即可。权限、副作用未知结果、Scope 隔离、35 轮覆盖许可和 System 独立保护均不能由公共包装绕过。
