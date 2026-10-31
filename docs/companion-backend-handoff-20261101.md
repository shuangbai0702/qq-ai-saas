# 前端交接：持续社交后端（2026-11-01）

前端由用户安排另一位 AI 在 Windows 独立修改。本轮后续工作不修改、不同步覆盖 `apps/web/`，不部署 Web 产物。API 与 AI 后端验收/部署继续；后端源码提交基线 `f379696`，后续修复暂未提交。

## 配置合同

`BotConfig.behavior` 新增 `autonomousGroupContact`、`selfDevelopment`，默认 true。`attentionEnabled`、`memorySemantic`、`proactiveModelSchedule` 也默认 true。沿用 `enabled` 总开关、`memory`、`proactive`、群白名单、静默时段、每日主动次数、每小时20次等依赖；不要隐藏已保存但依赖暂未满足的设置。

- 自主群联系：从本机器人实际见过、曾直接互动的群成员中选择对象，最多 @ 一人；群成员身份在入队和发送前核对，不回应/拒绝/告别会暂停。无手填 proactiveTargets 时，也可能合法地联系当前群成员；因此“targets为空就一定不生效”的提示应同时判断 autonomousGroupContact。
- 自身发展：人格喜好、自己表达过的偏好、共同话题热度、持续心境、未完话题；共用开关 selfDevelopment。共同话题热度不能展示成已确定的个人喜好。
- 长期动机V1：持久待续话题（最长7天）与有预算的模型日程，非自主浏览互联网或线下经历。
- 注意力：已有话题/人格兴趣/自然短接话；直接@和指令不受自动接话筛选影响。不是另开模型逐条审查群消息。
- 语义记忆：memorySemantic=true且embeddingModel为空时，同轮回复模型按含义阅读有界候选，不增加请求或独立等待。不要称为“已建立向量索引”。配置有效Embedding模型后仍使用既有远程向量索引与检索；沿用本Bot Key、出口检查与每日请求预算。界面勿把本地部署列为必要条件。

## 行为数据接口

沿用 `GET /v1/bots/:id/behavior-data?scope=group:群号&subject=QQ号&query=关键词`，租户鉴权与所属机器人校验不变。

新增可选字段 `companion`：

- `companion_interests`: scope/topic/weight/hits/updated/evidence/source。
- `companion_motives`: scope/subject/topic/due/expires/status/evidence。
- `companion_emotion`: scope/kind/intensity/updated/expires/neutral。

每类最多30条，scope筛选有效；旧库可能没有这些字段。`scope='*'`仅表示人格级偏好；`source=persona`为人格、`bot_expression`为自身曾表达、`conversation_heat`为共同话题热度。待续话题仅 status=open 且 expires>当前时间 时有效。不要把过期情绪显示成当前心境。

运行指标包括 semanticContextTurns/semanticContextCandidates、autonomousGroupAttempts/autonomousGroupSent/autonomousMemberRecheckFailed，均为当前运行周期统计，不能当作持久累计或QQ送达证明。

## 发布边界

API 与 Agent 独立。后端本轮需要 API 独立更新、共享AI空闲重载；Agent/NapCat与QQ Session保持。前端发布不得顺带重启这些运行实例。最终后端验收结果将追加至 companion-autonomy-20261101.md。
