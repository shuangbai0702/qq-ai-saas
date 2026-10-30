# 第三轮：大肥鱼来源、行为与复用边界

后续更新：话题召回、稳定/事件记忆与明确自述自动记录已经开发；当前状态及验证见 [各家机制整合](behavior-synthesis.md)。下文保留原轮交付时的研究与能力边界。

调查与实现日期：2026-10-28。当前模型 `gpt-5.6-luna`。这是基于开源 Persona 的还原版，未宣称与视频原作者运行实例完全一致。

## 先分清两条视频实现

普通视频页面曾返回 HTTP 412；直接连接 B 站公开 metadata API 返回 200。原始响应与 headers 保留在 `test-results/round3/bilibili/`，可以核对标题、作者与描述。没有完整观看视频，也未取得公开字幕；结论限于 metadata、互链和真实源码。

| 视频 | 作者与已核实描述 | 结论 |
|---|---|---|
| [BV1x2Jq6NEbT](https://www.bilibili.com/video/BV1x2Jq6NEbT/) | 凉风时雨；标题与描述明确基于 AstrBot，提到聊天聚焦、原生活跃概率和网盘人格材料 | AstrBot 路线。网盘提示词包本轮未下载，不能把另一个仓库人格称为视频原版 |
| [BV1jhn16GEcv](https://www.bilibili.com/video/BV1jhn16GEcv/) | AI技术投降派；描述直接链接 Yuuyuko-uu 的六个 DSH 插件 | DSH 插件路线，与前一个作者及框架不能混同 |

第二条的六个插件：tts-bridge、fish-memory、speak-up、bili-subagent、feiyu-mood、qq-bridge。描述中的效果比例属于作者陈述，未独立复现。公开 API 为 `https://api.bilibili.com/x/web-interface/view?bvid=<BV号>`；player metadata 返回无公开字幕。

## 源码与机制

| 仓库 / 固定提交 | 核心机制 | 本平台处理 |
|---|---|---|
| [whyever77/dafeiyu-astrbot](https://github.com/whyever77/dafeiyu-astrbot) `dc16e743281c09d9c91b09b470dd65179a5f2ad6` | v2.1 人格、表情、时间感知 | 作为主要人格风格来源；保留蓝色小鲸鱼娘、白饭偏好、短句及群友感，增加身份/记忆真实性约束 |
| [Yuuyuko-uu/dsh-speak-up](https://github.com/Yuuyuko-uu/dsh-speak-up) `28846d55052eb56b5294f78692b5227163124698` | 本地概率决定时机，再把事件交给主模型 | 自己的单个轻量 scheduler + 原生 AstrBot 事件，不另开一个判断模型 |
| [Yuuyuko-uu/dsh-qq-bridge](https://github.com/Yuuyuko-uu/dsh-qq-bridge) `6731c02295492f54b39e2617ed8c2292eb11bfd1` | QQ 发送节奏与控制 | 受控文本拆分、发送间隔及控制标记过滤；非原插件直接安装 |
| [Yuuyuko-uu/dsh-fish-memory](https://github.com/Yuuyuko-uu/dsh-fish-memory) `8b3571da94d8bafd12f746ad4b2035186f5c014c` | 衰减/选择性记忆设计 | 已研究；当前只实现可靠显式事实，不宣称实现原插件自动提取/半衰期 |
| [hth768/cortico-world-qq-final](https://github.com/hth768/cortico-world-qq-final) `0750487bb723f72157a8a8f7eb92d38242d54ef7`，0.6.35 | `src/proactive.ts` 的 local gate、host.pushEvent、未回复计数与预算；`world.ts` 的 QQ 行为 | 借鉴主动事件/预算/防循环。它依赖 Cortico World API，不能直接作为 AstrBot 插件启用 |

新快照在 `references/round3-behavior/`；旧快照在研究目录 `sources/`，保持只读。World 中的主动聊天不等于 Persona 插件的情绪/亲密度；两者 API 版本、职责不同。

## 当前真正工作的行为

提示词 `packages/contracts/fish-persona.ts` 负责身份、语言、关系边界、短句、诚实记忆与能力描述。程序 `adapters/astrbot/behavior.py` / `initiative.py` 负责决定是否调用模型、身份注入、持久化、发送和限额。仅写“你可以主动聊天”不会产生主动事件。

- QQ 的 bot/group/sender ID 来自协议事件，主人 ID 来自配置，不从正文猜测。
- 私聊、群 @、群白名单、冷却、概率插话在调用模型前判定。可选 @ 后 90 秒跟聊窗口，允许未再次 @ 的连续聊天；跨群/私聊历史分离。
- 可选 800ms 合并连续发言，一次调用包含最多 20 条可信身份消息；不是每条气泡机械问答。
- 可选最多三段普通文本、800ms 发送间隔。代码/列表不盲目切段。每次发送前复核运行代次，停止后迟到回复不得继续发送。
- `NO_REPLY` 真正阻止发送；过滤内部控制内容，有限 QQ 表情标记转 segment。
- 原生 AstrBot 最近上下文持久化；每 Bot SQLite `behavior.sqlite` 保存显式事实、来源、版本、简单关系和主动配额。
- `记住：饮料=绿茶` / `更正记忆：饮料=红茶` / `忘记：饮料`，按 Bot、会话、人物检索。限制事实数和版本，不运行 Embedding 服务。
- 简单好感与短时情绪由感谢/敌意事件更新并注入。尚未实现 DSH 七维情绪及复杂关系演进。
- 主动聊天仅发给明确配置且聊过的对象；群必须白名单，7 天无互动不打扰。安静时段、最小沉默、间隔、概率、日额度及未回复上限均持久化。尊重“别主动/晚安”等退出表达。
- 主动事件使用主 Persona、主 Provider、原生会话，不增加后台判断模型。先占用尝试额度，失败不无限重试消费 Token。

真实 QQ 证据：`real-companion-history.log` 和 `real-companion-telemetry.log`。先出现两条正常回复，随后没有新入站消息时主动聊起配白饭的小菜与红茶；SDK 记录实际模型、response ID、prompt hash、耗时及上游报告 usage。测试用 60 秒配置已恢复为正常 30 分钟沉默、1 小时间隔、每日最多 3 次、未回复最多 1 次，仅指向授权第二账号。

## 还没实现的体验

自动语义事实抽取、遗忘半衰期、完整七轴情绪、图片理解（上游测试 502）、语音、B 站浏览子 Agent、提醒工具、复杂主题规划和完整人格长期稳定性评测。20 场景合同在 `experiments/round3-persona/cases.json`；真实模型仅做有限代表样本，不能把 fixture 通过等同真人主观效果一致。

AstrBot 成熟主动插件仍可用于 dedicated 模式评估，但有全局配置、独立 Web/遥测及插件自己的存储作用域；共享模式不能直接让第三方代码读取同进程其他租户。先保持受控能力集，确有必要再适配作用域。
