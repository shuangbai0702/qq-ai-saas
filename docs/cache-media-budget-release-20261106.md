# FishBot 图片体积误计与追加缓存边界：实施验收

日期：2026-11-06。范围：核实低缓存审查中的关键事实，修复已证实的历史保留边界与统计口径，受控加载 Linux。不会把字符换算成 Token，也不宣称所有低命中已经消失。

## 1. 关键事实

从 2644 号真实 HTTP 请求取得同请求证据，原始采样只保存在服务器私有目录，不进入仓库：

| 指标 | 同一次请求 |
|---|---:|
| 消息 JSON 序列化字符 | 205,284 |
| 可识别普通正文字符 | 72,801 |
| 图片数量 | 6 |
| 图片 Base64 URL 字符 | 121,898 |
| 工具定义字符 | 4,378 |
| 上游 input_tokens | 36,040 |
| 上游 cached_tokens | 11,776 |

另一条真实请求仅有 41,648 正文字符，序列化仍达 170,225 字符。旧 ReadonlyJournal 将完整消息 JSON 与 160,000 字符比较，prepare 回退完整请求，commit 再拒绝保留。因此这不是“40 万 Token”的问题，也不是仅有统计展示错误：图片编码确实会错误触发传输历史重建。

外层另有独立但相关的边界问题：活跃基线约 12–14 万字符时，原总量 16 万限制留下的追加空间很少，早于既有 96,000 新增字符上限触边。首次超大状态还会被完整发送但无法保留；以往诊断未充分区分这些原因。

缺少上游 cached_tokens 的记录只能判为未报告。取消、PreflightRejected、请求未派发不能计作一次真实零缓存调用。工具定义在最终 provider 装配后才完整，因此最终 wire_request 才是工具体积/哈希的证据；较早 request 记录中的 tools=null 不证明实际无工具。

## 2. 实际修改

| 文件 | 职责与变化 |
|---|---|
| `adapters/astrbot/decision_history.py` | 统一有界计量函数；现用 AppendHistory 采用总文本 384,000、相对基线新增 96,000 字符限制；输出具体触边原因。 |
| `adapters/astrbot/readonly_journal.py` | 已识别图片 Base64 与非媒体文本分开计量；同样的文本/新增限制，另限消息序列化总量 2,097,152 字符；保留完整请求回退。 |
| `adapters/astrbot/model_cache.py` | 记录 wire/text/plainText/media 字符、图片/工具数量与工具体积、HTTP 字节量；超过诊断 4MiB 解析范围也留下有界 skip 元信息；构建核验支持类方法。 |
| `adapters/astrbot/behavior.py` | 前台和内层均记录 journal 实际计量；按 commit 返回值区分已保留与 not_retained。 |
| `adapters/astrbot/autonomous_runtime.py` | 外层记录基线、总历史、具体边界及提交结果。 |
| `tests/cache_budget_test.py` | 新增跨图片、文本/媒体硬边界、外层大基线增长、完整恢复与迟到提交验证。 |
| `tests/readonly_journal_test.py`、`tests/model_cache_test.py` | 更新实际新边界场景，验证最终 wire 工具体积与超大诊断行为。 |
| `docs/context-pipeline-contract-20261105.md` | 追加精确计量、限制和完整回退合同。 |

`textChars` 是序列化消息减已识别图片 data URL 的字符量，包含 JSON/工具调用等结构开销，不是纯正文，更不是估计 Token。`plainTextChars` 单独统计可识别正文。工具定义独立于消息统计。普通正文中的 Base64、未知结构仍保守算作文本。2 Mi 限的是字符，不声称等于 2MiB RAM；诊断的 wireBytes 才是 HTTP 字节。

不删除图片、不降低图片质量、不冻结情绪或目标、不改记忆内容。只有传输缓存保留上限变化；业务摘要仍默认 35 轮，传输保留仍 30 次调用/6 小时。身份、权限、摘要版本、遗忘、配置代次和群来源证明仍按原合同使历史失效。旧未使用的 DecisionHistory/SnapshotHistory 没有跟随放宽。

## 3. 真实 API A/B

使用用户已授权的自有测试 Key，实际含 6 张图片的请求；两组独立随机路由标记，各连续三次真实调用。每轮追加实际模型回答与下一条新消息，并更新只读资料。tool_choice=none，不执行任何 QQ/B站工具，不写人物记忆。不是预填虚假 assistant 回复的缓存测试。

| 组/轮 | input | cached | 命中率 | 追加历史保留 | 整体请求耗时 |
|---|---:|---:|---:|---|---:|
| 旧版1 | 26,076 | 未报告 | 未知 | 否，size_boundary | 40.576s |
| 旧版2 | 26,133 | 11,776 | 45.06% | 否 | 3.356s |
| 旧版3 | 26,189 | 11,776 | 44.97% | 否 | 4.208s |
| 修复1 | 26,081 | 未报告 | 未知 | 是 | 7.822s |
| 修复2 | 26,174 | 25,856 | 98.79% | 是 | 2.699s |
| 修复3 | 26,262 | 25,984 | 98.94% | 是 | 3.503s |

温启动两轮合计未缓存输入：旧版 28,770，修复 596，下降约 97.9%；总输入基本相同。这证明收益来自保留前缀，没有靠删除必要信息。只代表这份轨迹；冷启动延迟有明显波动，小样本不能证明 P95。上游未提供实际费用明细和 TTFT，费用/TTFT 没有独立验收，不能用 Token 数冒充实际账单。

服务器证据：`/control/cache-boundary-release-20261106/api-ab-results.json`、`capture-summary.json`。私有原始请求不随报告公开。

## 4. 回归与运行验收

Linux 现有 AstrBot venv 执行：81 个 unittest 通过，外加 `foreground_prompt_test.py` 的真实 provider 装配断言全部通过。覆盖 cache_budget、readonly_journal、decision_context、decision_history、model_cache、context_transport、architecture_contract、native_history，以及独立前台装配入口。

验证内容包括：原生与工具结果顺序、完整状态重建、字段删除、群轮次归属、失败候选不清除已提交历史、迟到提交不能覆盖新代次、忘记后的前台边界、内部任务/被动入口区别及超大完整回退。

Windows 的隔离核心测试通过；完整 AstrBot 相关测试因本机默认 Python 缺少 aiohttp/deprecated 等依赖未通过该环境执行，已改用 Linux 真实依赖环境完成，不能称 Windows 全量回归通过。最初将有启动级 monkey patch 的测试放在同一 Python 解释器内导致类身份干扰；改为每套独立进程。foreground_prompt 是直接脚本断言，使用其实际脚本入口执行，没有跳过断言或将 unittest 的“0 tests”冒充通过。

上线后真实结果：

| 链路 | input/cached | 证据 |
|---|---:|---|
| 小双白首次私聊 | 19,184 / 未报告 | 真实入站、确认回复；首次加载冷上下文。 |
| 小双白第二条私聊 | 20,810 / 18,944，91.03% | journal=append，实际保留 28 条共同前缀；QQ 两轮均 commit/sent。 |
| 2644 内层任务首次 | 15,259 / 未报告 | 实际任务入口。 |
| 同任务后续工具循环 | 15,591 / 15,104，96.88% | 实际工具结果追加，journal=append。 |
| 2644 外层 planning | 60,973 / 51,840，85.02% | 实际线上请求；外层继续正常决策。 |

另有 orientation 未报告缓存字段，不能算作 0，也不能算作命中。未声称跨任务首请求和全部前台/群聊都已达到 99%。

## 5. 部署与资源

2026-11-06 17:35–17:36，通过已有 ProcessHost 所有权控制逐组加载两个共享 AI，等待原生入口排空；未更新 API/Web/Node ES 模块。原协调器恢复调度，没有额外安装其他能力。Agent PID 730881 保持；5 个 NapCat PID、创建时间及 Node ChildProcess 对象均保持；共享成员分别 4/1，均 ready、memberErrors 为空。新 AI PID 1844595、1844700。

发布 ID：`cf279f4d109d6c7164f47696a75d689ebdf2d3232aa3275504ced8a976a852e0`。source/runtime 原子发布后校验一致，实际运行 model_cache 函数指纹与磁盘相符，新的 text/media 诊断已由生产前台与内层路径产生。启动时尚未被 import 的 journal 类不会虚报已加载；有实际调用的入口再以新增诊断核验。闲置组没有伪造模型调用来制造已执行证明。

核心源码 SHA256：

```text
decision_history 41ead30c16c951e6d25a32ef7ee99ac1a891c4e68a1094b30f364eb1bfdaf058
readonly_journal 6eeb2852a6a65b24053b45f024f21231f5bbe94d9ec1122deec532d78205be32
model_cache      ff24ed5ab5e2afc8ef60f871a784b4844ef5d25fc43113734402027bb3b9d69b
behavior         3e473fea9817a42aea15c948ed7a0807bc8471fe5c67d79ba62d4c401d627a87
autonomous       8e5a50bd4192b2b7c9ec17e818ae2620da4338e7b192a7b834a1b5bd14da2e45
```

未改 Guard、PAGEOUT、Swap/zswap/cgroup 代码或配置；闲置共享组实测 memory.high=50,331,648 字节（48MiB），memory.max=max；活跃组临时 high=max 仍由原活动机制管理。zswap enabled=Y、shrinker_enabled=Y、max_pool_percent=25。

以 total−available 统计整机 RAM：部署前约 1,212.7MiB，重载及真实消息测试后约 1,403.6MiB；Swap 约 1,420.7→1,133.4MiB。重载/活动将部分页面换入，不能宣称本次降低整机 RAM；这不是控制负载的稳态内存 A/B。随后现场 PSI some/full avg10 为 0.36%/0%，仍需自然负载观察；不以瞬时 PSS 证明容量或无泄漏。

## 6. Plan vs Actual、回退与余项

- 已完成关键事实同请求核验、媒体/文本计量修复、现用外层基线边界放宽、提交状态诊断、回归、真实自有 Key A/B、受控 Linux 加载和两条真实 QQ 回复。
- 没有改写原本可靠的 ContextBuilder、群私记忆、来源/权限校验或追加语义。没有将历史内容删掉来制造命中。
- 最初候选考虑把前期工具表纳入 journal stamp；实际核实工具在更晚阶段装配，未采用这个方案，以免合法工具变化反而引入额外重建。最终工具变化仍由 wire 哈希/计数诊断并由原授权工厂控制。
- 未把工作树里已有的其他前后端/架构修改归为本次产出，也未对这些修改执行批量 Git 提交。本次发布只覆盖明确核对的相关 Python 文件及测试。
- 回退目录：`/opt/fishbot-pg-cutover-20261031/control-final-c/cache-boundary-release-20261106`，包含 before/source、before/runtime、preflight、发布和恢复脚本。先经同一维护入口 hold，校验后用 publish.py rollback，再经 recover 恢复 AI；只恢复源码，不用旧数据库覆盖新增聊天。不能只回滚磁盘后声称运行时已回滚。
- 尚未验收：长期 P95、稳态 RAM/泄漏、全部会话自然负载的整体命中率、实际账单费用、上游缺失缓存字段的供应商侧原因；现有合法的摘要/身份/权限重建与服务端冷启动仍可产生低缓存。
