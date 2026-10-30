# 社交能力代码复查与修复

2026-10-28，工作区 `D:\MYCODE\APP\fishbot\qq-ai-saas`。

范围：上一轮新增的表达学习、图片发送、图库 API 和网页请求。使用 async-lifecycle-audit 与 plan-execution-review；没有把本次局部检查表述为全仓安全审计，也没有修改旧 Coopanion。

## 发现与修复

| 问题 | 根因及实际影响 | 修改 |
|---|---|---|
| 过期表达仍被召回 | 只有再次接受学习结果才清理30天旧表达，闲置群仍会注入旧表达 | `social.py:expressions` 在读取时过滤有效期 |
| 旧样本仍进入学习 | `learning_batch` 只看 ID 水位，没有按7天时间窗过滤 | 学习查询同时限制 created；不会等下一次写入才避免使用过期数据 |
| 清理样本后学习卡住 | SQLite 普通 INTEGER PRIMARY KEY 在删空后复用旧 ID，而学习水位仍高于这些 ID | 使用 AUTOINCREMENT；兼容迁移旧表，保留原样本ID，并以既有学习水位初始化序列 |
| 迁移中途失败可能留下半迁移状态 | Python SQLite connection context 本身不为 DDL 自动开启事务 | 显式 BEGIN IMMEDIATE，将重命名/建表/复制/删除/序列更新放在同一事务；故障注入验证回滚 |
| 发送迟到完成可重新创建旧状态 | `await original_send` 返回后直接调用 store()，未再次核对所属 setting；成员可能已停止或被替换 | 每次发送 await 后再次检查 setting 对象及停止状态，旧回调不重建 SQLite、不更新新代次统计；已经提交给协议端的消息不能撤回 |
| 图库列表与删除并发可能500 | readdir 得到文件名后，删除接口可能先删掉对应JSON，列表再读取时报 ENOENT | 同一 Bot 的列表读取与既有增删共用顺序锁；不同 Bot 仍可并行 |
| 初始图库响应迟到覆盖新上传结果 | 页面只有组件退出的 AbortController，没有同一页面内的操作顺序 | 增加操作序列；旧初始GET不覆盖后续上传/删除结果，并用同步 pending 引用阻止同帧重复提交 |

前三项已在修复前复现，原始失败记录 `test-results/social-audit/before.log`。迁移异常回滚另有修复前失败记录 `migration-before.log`。没有只靠“看代码应该如此”就标成已验证。

过期读取过滤不等于闲置数据库物理删除调度：实际物理清理仍由原有写入清理执行，本次保证的是过期数据不再进入学习或表达召回。

## 验证入口与范围

- `tests/social_test.py`：23项场景，包含过期读取、旧样本过滤、ID不复用、旧表数据保留、迁移故障回滚、原有作用域/额度/取消测试。
- `tests/send_lifecycle_test.py`：真实 AstrBot SDK 的发送包装器，协议发送 await 使用 barrier；稳定构造停止/替换后完成，断言没有新建旧 Store 和旧统计。该测试没有真实QQ网络发送。
- `tests/social.test.ts`：原有权限、上传、删除，加上同一 Bot 列表/删除并发检查；在额度内测试，并另行断言第11次上传尝试仍为429。
- `tests/browser-social-race.ts`：独立数据库/API＋生产网页，固定挂起初始空列表，上传并显示新图后再返回旧响应，图仍保留；没有使用真实客户数据库或发QQ消息。
- `tests/social-runtime.test.ts`：原生 AstrBot SDK＋OneBot WebSocket，学习、隔离、图片、额度和沉默回归；模型/QQ传输为替身。

本轮全量首次执行因新增并发测试超过已有10次/分钟上传上限而失败，原始记录 `full-tests-first.log`。修正测试请求数，并单独验证超限429；没有降低接口限流或跳过检查。

最终运行结果与服务更新记录在 `test-results/social-audit/`。本轮没有主动调用真实模型或向QQ发送新的人工验收消息，既有主动功能保持原配置；此前真实QQ发图证据仍保存在 `social-integration/`，不能把它当作本轮新发生的真实网络验收。

## 最终实际结果

- 最终 `npm test`：36通过，0失败、0跳过，253.96秒；包含23项 Python 社交场景的包装测试，以及真实 SDK/OneBot 回归。原始日志 `full-tests.log`。
- `npm run build:production` 和 `npm run check:production` 通过，日志 `build-final.log`、`check-production.log`。
- 独立浏览器 barrier 测试通过，`browser-race.json`；更新服务后的完整网页回归也通过，`browser-final.log`。
- 原有2份 Bot 行为数据库完成迁移，事实表逐行比较未改变（3/0行）；各库迁移前已做 SQLite backup，日志 `migration.json`。平台 PGlite/Master Key 另备份到 `.local/backups/social-audit-platform-20261028-185336/`。
- 本地生产入口仍为 `http://127.0.0.1:8787`，API PID11632。两台已有真实QQ自动恢复在线，共享AI就绪，保存/生效版本分别9/9与1/1；只读记录 `runtime.json`。没有要求重新扫码。
- 复查差异与源码哈希保存在 `review.diff`、`change-review.json`；没有修改与本次检查无关的产品实现。

## 范围和后续优化

没有增加运行进程、本地Embedding服务、复杂插件层或新公共缓存。主要收益是状态正确性和避免无效/过期学习；没有同条件内存压测，不能宣称省了多少MiB。

仍可继续优化：上传PNG目前为头尾/尺寸等基础格式检查，可补完整结构/解码验证并评估解码峰值；图库遍历和JSON读取量当前很小，扩大图库前再实测是否需要有界缓存；长期表达质量、自动识图收录和Linux容量仍未完成。本轮不借复查扩大成全框架重写。

## Plan vs Actual

| 要求 | 实际 | 状态 |
|---|---|---|
| 检查新增功能调用链和隔离 | 检查 SocialStore、send wrapper、图库锁、网页请求生命周期 | 完成局部复查，不代表全仓无缺陷 |
| 明确缺陷直接修复 | 上表7项修复，含旧数据迁移 | 完成；无新增产品功能默认开关 |
| 有复现和回归证据 | 修复前失败、故障注入、发送barrier、浏览器barrier、接口并发及原生运行回归 | 完成；最终36项全量通过，详细范围见上文 |
| 保护现有数据/改动 | 修改前副本 `experiments/social-audit-baseline-20261028-183619/`，仅修改相关文件 | 未执行Git清理或覆盖无关项目 |
| 优化资源成本 | 不增加常驻服务，限制无效学习；未进行新的容量测量 | 未承诺或宣称内存降幅 |
