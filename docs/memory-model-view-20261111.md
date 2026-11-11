# 长期资料模型阅读表示优化（2026-11-11）

## 实施依据与范围

用户目标：保留 relations、facts、protected_claims 的全部有效语义、权限、身份、时间和执行能力，减少重复字段与结构包装，降低 Provider 输入 Token。只调整模型阅读表示，不删除记忆、不压缩事实正文、不调整数据库。

调查后确定的实施计划（实施前已在会话说明）：使用可还原的表格表示，把同一列表的公共值提取一次，逐行值按列排列；只改变新生成的模型资料包。保留内部完整快照、旧 Journal、状态更新和工具执行合同；验证完整还原、增量、旧前缀及最终请求，再测量 Token。

## 实际实现

- `memory_model_view.py` 提供纯复制视图及 JSON 输出。`format/meaning/common/columns/rows` 自描述表示完整对象数组；不摘要、截断或合并记录。字段缺失与 null 不混同，列表行序、重复记录、嵌套值和未知字段值保留。单条、异构、非对象列表及编码后不更短的列表沿用原形。
- `context_foundation.arrange` 仅为新建或已有合法许可的重建候选生成固定基础区和资料包。`readonly_journal` 仅编码新追加包，旧 wire 前缀原样复用；state、authority 和 CAS 快照仍是原始结构，持久化 wire 则精确记录实际提交的表示。System、消息顺序和工具链不变。
- `decision_request` 覆盖辅助判断的 reference 与动态资料；`Reflection.retry` 在同一次整理请求最终序列化时编码 existing_memory 与 previous_commitments，不改整理前快照、版本校验或输出应用流程。
- 核心便签中的 protected_claims 也可编码；事件 authority 的其他身份与权限字段原样保留。表格说明明确要求先展开，再按 readonlyChanges/eventChanges 原路径更新，后续单字段状态修订无需重发完整列表。
- 遍历限定于已知记忆容器，不遍历工具结果、正文、回执或任意用户 JSON。选择数量、既有预算、检查点时机、有效约定执行入口均保持原实现。

## 验证

以下 9 个测试入口合计 112 项通过：

`memory_model_view_20261111_test.py`（6）、`context_foundation_20261109_test.py`（5）、`context_transport_test.py`（30）、`decision_context_projection_20261109_test.py`（4）、`readonly_journal_test.py`（27）、`journal_storage_test.py`（8）、`semantic_provider_wire_test.py`（2）、`memory_delta_operations_20261111_test.py`（7）、`semantic_fact_relation_operations_test.py`（23）。

新测试涵盖三类列表完整还原、可选字段与类型、未知嵌套值、输入对象不变、工具/回执不变、辅助资料包、原始 Journal 状态、取消状态增量、已提交前缀及核心便签事件原始快照。整理测试走真实固定 SDK 和 HTTP MockTransport，在最终 HTTP JSON 中验证表格存在，并还原比较完整 existing_memory 快照；未连接外部模型。

额外运行 `d4_claims_test.py`：9 项中 4 项失败。将本次 `memory_json` 在进程内替换为原 `model_json` 后，原样复现同 4 项失败：旧 event 字符串预期与 semantic 哈希不一致，以及 summaryReleaseHolds / summaryProtectedConflicts 计数预期不满足。未改动这些测试或 Claims 持久化逻辑，不将其列为通过。

## Token 证据及限制

使用新测试中每类 20 条的合成样例，计数包含表格说明与包装。样例有公共来源和重复语义值，结果不能代表生产分布。

| 资料 | o200k_base 原形 → 新表示 | cl100k_base 原形 → 新表示 |
|---|---:|---:|
| relations | 1285 → 302 | 1404 → 315 |
| facts | 1285 → 379 | 1464 → 432 |
| protected_claims | 1736 → 469 | 1795 → 460 |

计数来自本机 tiktoken；生产代码不新增 tokenizer 依赖。运行时回退比较的是 JSON 字符长度，不能保证所有 Provider/tokenizer、所有资料分布都减少 Token。最终 HTTP 结构已验证，但尚未测量线上 Provider usage、缓存或账单，也未进行真实模型理解/工具选择对照。旧会话固定基础要等现有合法检查点才采用新表示，不能立即据此推算线上节省。

## Plan vs Actual

计划中的三类可还原表示、新请求边界、原始状态/旧前缀保护和相关验证均落实，未修改数据库、权限、记忆筛选和清理合同。实际额外覆盖整理模型与核心便签事件资料，因为调查确认它们也是相同记忆的模型阅读入口，复用同一纯视图，没有增加模型请求。

Token 验收采用本地 tokenizer 和 SDK HTTP 结构证据；线上计费收益及真实模型理解验证仍未完成，不能宣称实际 Provider 成本验收完成。Claims 旧测试失败已做禁用本变换对照，保持现状并明确报告。未部署、提交或推送；工作区原有修改不属于本任务产出。
