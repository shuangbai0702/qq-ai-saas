# 吸收各家机制：源码比较与第一批实际整合

日期：2026-10-28。工作区仍为 `D:\MYCODE\APP\fishbot\qq-ai-saas`。本轮围绕“保存每个人信息、聊起来像人”继续开发，不重新选框架。原版本备份在 `experiments/memory-integration-baseline-20261028-155133/`；上游参考目录只读，旧 Coopanion 未修改。

## 1. 看了哪些，取什么

固定提交、实际本地位置见 `test-results/memory-synthesis/upstreams.json`。源码是以下比较依据，功能名称不代表已经完成我们平台的验收。

| 项目 | 具体源码与长处 | 本平台取舍 |
|---|---|---|
| [DSH fish-memory](https://github.com/Yuuyuko-uu/dsh-fish-memory) `8b3571da` | `lib/index.js` 的 `鲜活度`、`evaluate`、`recall`：稳定事实不衰减；事件按时间变化；相关性决定能否进入，鲜活度决定排序；带来源与时间 | 本轮落实稳定/事件分类、话题优先、来源时间和注入预算。使用独立 SQLite 实现，不宣称移植了完整词典、remember 工具、自动提炼或作者命中率 |
| [AstrBot LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) `7cdd88a1` | `main.py:402/415` 区分召回和反思；`core/memory_scope.py` 处理身份/作用域；`retrieval/bm25_retriever.py` 中文二元组；诊断分离 | 本轮采用中文二元组辅助、作用域查询和可解释召回原因。完整插件的向量/图/FAISS/管理页面未加载进共享进程，先保留轻量常驻成本 |
| [QQ-agent](https://github.com/K0nd1us/QQ-agent) `789c7f75` | `0.4/src/memory.js` 每会话每群友印象、更新/删除/整理；`src/prompt.js` 明确发送、沉默和短句节奏 | 人员资料始终绑定真实 QQ ID；继续保留短句/沉默/分条。不能照搬“普通文本全是思考，必须工具发送”的提示词，否则与 AstrBot 的原生输出协议冲突。0.5 标注 unfinished，不能按版本号认定成熟 |
| [cortico-world-qq-final](https://github.com/hth768/cortico-world-qq-final) `0750487b` | `src/proactive.ts` 本地候选、未回复、预算、持久状态，再触发主 Agent | 前轮已实现相近主动门控、未回复和安静时段，本轮保留。World 接口不属于 AstrBot，不能直接装进来 |
| [DSH speak-up](https://github.com/Yuuyuko-uu/dsh-speak-up) `28846d55` | 本地决定主动时机，复用主模型 | 已有单 scheduler，不加一个常驻模型判断是否说话 |
| [DSH QQ bridge](https://github.com/Yuuyuko-uu/dsh-qq-bridge) `6731c022` | 消息桥与发送节奏 | 已有分条、间隔、代次校验；语音桥未实现，不能仅复制文本标记就宣称语音可用 |
| [DSH feiyu-mood](https://github.com/Yuuyuko-uu/dsh-feiyu-mood) `4e210244` | `lib/index.js:1059 judge` 用模型判断；`judgeChain/judgeVersion` 串行提交并拒绝旧归属回写；七维情绪及衰减 | 模型评估比关键词更有表达力，但会增加请求/Token，且需租户作用域。本轮没有把它整套启用或把当前轻量情绪冒充七维系统。后续应做有额度、可关闭的模型评估 |
| [dafeiyu-astrbot](https://github.com/whyever77/dafeiyu-astrbot) `dc16e743` | 人格 v2.1；face_convert / bili_activity / schedule_awareness，日程注入按 unified_msg_origin 过滤，避免编造安排 | 继续采用其人格风格。提醒、B站、语音依赖真实工具与供应商，后续优先适配原生插件；共享前审查全局配置/存储，不直接全开 |
| [Fatfish](https://github.com/Baimenlou-code/Fatfish) `1b05b48f` | 本地桌面/命令行 Agent，文件、命令、工具和网络能力 | 名称相似不等于 B 站 QQ 机器人同源。桌面文件/执行命令不是云托管群友的默认能力，本轮不搬入 |

选择的是可验证的机制。并非给共享 Worker 装上所有插件；跨框架提示词、工具协议和全局状态可能互相冲突。

## 2. 本轮真正进入代码的能力

新增 `adapters/astrbot/hosted_memory.py`，由现有 `BehaviorStore` 调用，dedicated/shared 都使用这一层。

1. **按问题找旧事实**：查当前 Bot 的当前会话/人物最多 200 条有效事实，中文二元组、英文词、字段别名和词的区分度帮助排序。生日、称呼作为基础资料候选。相关旧事实不会只因不在最近八条而消失。
2. **稳定资料与事件区别**：普通 `记住：字段=值` 是稳定事实；新增 `记住事件：字段=值`。事件鲜活度随时间递减，提起后强化；相关性仍决定召回，不因旧事件过期就删除原记录。衰减主要影响多个相关结果的相对排序，不是完整自动遗忘系统。
3. **自动记录明确自述，需开关**：识别“我叫小明”“我的生日是5月1日”“我现在住在杭州”“我的职业是程序员”“我喜欢喝红茶”“我喜欢吃白饭”等有限结构。疑问、引用、假设、第三人称、含糊或复杂句不写入。不是一般自然语言自动理解。
4. **纠正优先级**：自然自述可更新先前自动资料；不能覆盖手动明确记录。`更正记忆：饮料偏好=红茶` 可人工修正。相同值不叠加版本，旧重复消息不能把更正回滚；保留每字段最近十版。
5. **有界成本**：每人每会话最多 200 有效事实、Bot 总事实行仍最多 20000（含版本）；每人每会话每天最多 20 次自动更新，手动纠正不消耗自动额度。一次最多八条，字段名与值合计最多 1600 字符，额外元数据另计。不是精确 1600 token 限制。
6. **来源与解释**：注入包含 origin、source_event、remembered_at、kind、recall_reason。自述是当事人声明，不等于外部核实；事件不等于永久偏好。更新人格说明，避免承诺未写入的记忆。
7. **网页配置**：基本配置新增“自动记住明确的自我介绍”，长期记忆关闭时此开关禁用。旧配置默认不启用自动提取；相关性召回升级自动生效。当前授权 AI 测试 Bot 可单独启用，不替其他 Bot 修改设置。

只增加兼容 SQLite 表与索引，不删旧 facts、不重写主平台数据库、不改 QQ 登录态。跨群/私聊暂不合并人物档案，以免把私下说的事情公开到群里。同名群友按 ID 区分。

## 3. 实际验证和性能

- 旧 Python 行为 16 项保留，新记忆单测 18 项覆盖 opt-in、自述误判、手动优先、纠正、旧相关记忆、别名、跨 Bot/群/人物隔离、旧库重启、重复事件、额度、预算、事件和版本保留。
- `tests/memory-runtime.test.ts` 启动真实共享 AstrBot、真实 SDK 和 OneBot；QQ/模型是本地替身。两个 Bot 用不同 Key/生日，同一 sender 的生日分别进入各自模型请求；私聊资料不进入群，其他 sender 读不到。测试检查新注入的 JSON，不靠历史中偶然出现的词判断成功。没有额外后台提炼请求。
- 原始失败保留：`memory-synthesis-unit.log` 记录“我爱喝什么”误入资料，修正后严格排除疑问；`memory-synthesis-runtime.log` 记录测试误把 AstrBot 后续 system 段也解析为 JSON，已按真实单行注入合同修正测试，不删除断言。
- `experiments/round3-persona/recall-benchmark.py` 在独立临时库用相同 200 条事实/100 次查询对比：旧版中位 2.31ms / p95 2.66ms，生日未召回；新版 5.59ms / p95 6.15ms，生日召回。Windows Python3.12 单次探针，不代表 Linux 容量、模型质量评分或长时间性能。
- 原始数据与上游提交：`test-results/memory-synthesis/`；本轮过程日志也保留在 `test-results/round3/memory-synthesis-*.log`。最终完整测试/构建/浏览器和真实 QQ 结果在同目录追加，不把替身验收写成真实。

复现：

```powershell
npm test
npm run build:production
npm run check:production
references/astrbot/.venv/Scripts/python.exe experiments/round3-persona/recall-benchmark.py --baseline experiments/memory-integration-baseline-20261028-155133/adapters/astrbot/behavior.py
```

## 4. 下一批应取的长处

**先做记忆管理和人物档案**：在网页按群/人物查看、修改、删除，给自动提取结果明确的来源和撤销入口；若允许跨群共享，使用显式可见范围，不靠昵称自动合并。

**再做受控自动提炼**：优先借鉴 DSH remember 和 LivingMemory 反思接口，把同一主模型提出的记忆作为候选，由程序校验人物、作用域、证据、版本及额度。长句推理、事实冲突和高风险误记需要真实评测，不能通过加几个正则假装完整实现。

**情绪、日程和媒体逐项接入**：情绪判断限制频率/预算；日程必须来自真实调度记录；语音/看图先验证供应商。dedicated 可以优先适配完整成熟插件，共享模式只放经过作用域审查的受控适配。

## Plan vs Actual

| 本轮范围 | 实际 | 边界 |
|---|---|---|
| 深看各项目、选有效机制 | 九份固定源码快照，比较到函数/文件，清楚标注同名不同项目 | 没有声称重新验证了所有上游功能/视频效果 |
| 改善“记得每个人” | 相关召回、有限自述、稳定/事件、纠正与预算、网页开关进入代码 | 不是全语言自动记忆；没有跨群人物合并、网页事实管理、通用向量语义检索 |
| 保护既有能力与租户隔离 | 兼容旧库、保留旧测试；新增 native SDK 隔离验证 | 仍然是受控共享进程，不是任意插件沙箱 |
| 资源目标 | 无新后台模型/常驻依赖，有同条件局部 CPU/延迟对比 | 未重测完整 Linux 容量，不沿用前轮内存数值冒充本次实测 |

整合状态与最新执行结果以本文件末尾的本轮核验记录为准，历史第三轮报告保留其当时边界。

## 本轮最终核验记录

- `npm test`：**32 项通过，0 失败、0 跳过**，239.3 秒；`full-tests-final.log`。其中保留旧行为 16 项子单测，新增记忆 18 项子单测，不能把不同层级计数混为 Node 测试总数。
- `npm run build:production`、`npm run check:production` 均通过，日志为 `build-production-final.log` / `check-production-final.log`。
- 浏览器真实 API 验证通过，`browser-final.log`；新增自动资料 checkbox 勾选、保存、刷新后仍启用断言。截图 `test-results/browser-config.png` 已查看，中文显示正常。修正原模拟横幅“独立真实进程”的不准确文字为“真实运行实例”，共享模式由右侧实际组状态说明。
- 已在现有 8787 服务部署。授权 AI Bot revision8 / applied8，自动资料开启；第二 QQ 的配置未改，仍作为无模型 Key 的接收测试号。最终快照 `final-live-status.log`：两 QQ 在线、同 AI PID57328；API PID83760。PID 只记录当时状态。
- **真实 QQ 两条输入验证**：先发“我最近都喝桂花乌龙”，这一轮模型连接失败，但规则已持久化资料；再问“我爱喝什么？请只说饮料名。”，真实 `gpt-5.6-luna` 回复“桂花乌龙”。第二轮 response ID、prompt hash、QQ 时间与消息 hash 在 `real-qq.json`；第一轮失败明确保留，不能称两轮模型都成功。
- 只读 SQLite 核验 `durable-self-report.json` 确认资料确实保存为 self_report，并非仅靠短期聊天上下文作答。此表在 Bot 根目录 `behavior.sqlite`，dedicated/shared 共用相同 Bot 路径。
- 真实测试暴露原生 AstrBot 把 APIConnectionError 直接发到 QQ 的问题。已修复在行为适配层：异常标记与失败类型留在 metrics，发 QQ 时改成简短连接失败提示，不泄露上游异常，也不假装生成成功；不增加重试。native SDK 回归用本地 500 错误响应验证未泄露错误内的合成 Key、QQ 不被误标掉线、下一次输入可恢复。**错误渲染修复已部署；未故意再次使真实模型故障。**
- `change-review.json` / `review.diff` 记录相关文件变化与基线哈希。因工作区不是 Git checkout，未虚构 Git diff 或提交；所有上游快照仍未修改。

本轮额外修复错误渲染和模拟横幅，是运行中发现的具体缺陷；不改变共享边界或模型调用协议。未收口仍是通用语义提炼、网页记忆管理、七维情绪/语音/B站适配，以及 Linux 完整容量与长期验收，没有通过本次局部性能测试把它们标为完成。
