# 03:51 / 04:07 成对低命中复核与修复

## 时间线与事实

用户提供：03:51约34.9K/333、38.2K/179；04:07约41.5K/316、44.2K/284，后台未显示缓存收益。PostgreSQL实际周期：

- 03:51:26.758–03:51:58.636：26号d18ad…，2次模型请求、73643总Token、0工具，completed/sleep。
- 04:07:22.824–04:07:58.295：小双白7356…，2次模型请求、86210总Token、0工具，completed/sleep。
- 03:48:15/03:48:45受控替换共享AI以加载上下文精简候选。以上各自是对应机器人在该次加载后的首个自主周期。

外层orientation和planning各有独立AppendHistory与路由key，复用目标是上一周期同阶段，不能要求同周期两个不同阶段互相完全复用。autonomous_runtime初始化decision_histories={}，新进程首次请求必须建立新基线。可以证明这些调用触发应用首轮新历史；当时未保存原始HTTP包，因此不能精确证明上游cached_tokens显式为0还是字段未报告，也不能解释所有历史零命中。截图的输入/输出/时间与数据库总量吻合，原包缺失限制保留。

## 进一步发现的重复失效边界

旧AppendHistory.MAX_IDLE_SECONDS=1800，本地prepare时间为基准；小双白上轮03:36:43.609开始、03:37:18.670完成，下一轮04:07:22.824开始。睡眠30分钟再加模型/调度耗时，间隔必然超过1800秒。即便不部署，也会撞idle_boundary，两个阶段同时重建。它与32K/96K字符边界独立。本次04:07又恰逢部署后首轮，不能将两种原因混为唯一历史证据。

此外，shared-worker.py为避免共享框架日志泄露跨租户正文，明确logging.disable(logging.CRITICAL)。此前新增logger.info的FISHBOT_DECISION_CONTEXT和FISHBOT_MODEL_USAGE因此不会落到共享日志，实际扫描也未发现这两类记录。不是因为缺日志就能推断没有模型请求；实际周期由PostgreSQL证明。不能打开全部共享日志解决。

## 实际修改

1. decision_history.py：仅AppendHistory使用MAX_APPEND_IDLE_SECONDS=21600（6小时），其余旧历史类仍1800秒。外层本地传输连续性不再因正常30分钟睡眠主动失效。不是声明API缓存TTL为6小时，也不改变自主唤醒/模型行动策略。
2. 30轮、160000总字符、96000新增字符、当前状态重建投影验证、摘要/遗忘/租户/身份/配置/提供商变更失效继续保留。原始记忆和总结不冻结。
3. model_cache.record_diagnostic：由可信租户bot/root选择位置，写入机器人私有model-diagnostics.jsonl；白名单字段仅时间、PID、bot、代次、周期/Work、阶段、system哈希、重建原因、历史数量、尾长和上游真实Token/cache字段。不存在正文、Key、Cookie、提示词。单文件256KiB达到后转上一份，最多两份，不加载整段日志到内存。IO失败不改变模型结果。未知cachedTokens仍None，不补0。
4. autonomous_runtime/native_internal接线：上下文准备与真实模型返回分别记录，保持共享原生日志禁用；RunControl只携带选择后的诊断归属字段。

## 验证与部署

Windows：decision_wire9、decision_history7、model_cache6通过；SDK native_internal13通过；完整autonomous_pipeline脚本完成exit0。Linux隔离候选上述9+7+6共22项通过。新增覆盖30分以上睡眠保留精确前缀与最新状态、6小时边界仍失效、身份/摘要及时失效；日志字段裁剪/租户归属/轮转/IO失败隔离。

04:39:58/04:40:16通过现有ProcessHost空闲边界加载两个共享AI（1676681→1687172，1676747→1687244），成员4+1均ready。Agent730881和5个QQ协议PID/出生值保持，5QQ在线。source/runtime10目标原子发布与哈希校验；实际导入appendIdleSeconds=21600、otherHistoryIdleSeconds=1800、addedChars=96000、totalChars=160000、turns=30。未修改前端、账号数据/Key/QQ Session，不执行Git写操作。每实例48MiB/40秒内存治理不改。

发布与回退保存在/control/cache-sleep-fix-20261106-release，含before/、preflight、发布/ProcessHost恢复脚本。回退不还原数据库或新增聊天，只恢复匹配哈希源码并受控加载AI。此次加载后首次周期会再次冷启动，不把它当连续复用失败。

## Plan vs Actual 与未验收项

用户要求对照两组同型低命中：已对应机器人/周期/阶段性质，查明与新版本首次周期重合；原始包缺失，不能声称已逐字比对当时请求。扩大为修复30分钟空闲与正常睡眠冲突，以及补齐被共享日志屏蔽的内容无关诊断，属于已持续授权的缓存修复范围。未引入第二套记忆、缓存副本或模型调用；无额外付费API探针、伪造QQ消息或修改人物配置。

仍需后续自然周期实测：新日志实际返回、同阶段前缀延续、上游显式0与未知的区分、真实长期缓存率；尤其未宣称6小时后上游必命中，或所有零命中已解决。API供应商自己的缓存保留/路由未知，不能用本地上限替代验证。
