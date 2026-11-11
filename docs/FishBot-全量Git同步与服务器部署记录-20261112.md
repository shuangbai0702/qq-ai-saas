# FishBot 全量 Git 同步与服务器部署记录

日期：2026-11-12。依据用户追加指令“提交推送到git，然后服务器部署”，以及明确范围“整个，包括刚刚别的AI改的”。

## 发布范围与版本

本次部署整个已完成生产代码，包含系统收敛、上一轮整洁度整改、本轮架构简化，以及另一维护任务刚提交的提示词、认知产品、合法记忆检查点和群判断 8192 token 上限修复；没有只部署某一个智能体的文件。

- 原生产实际构建：`b41d10881b6bdeeeb94b58cf7a2cd7b0869ed6f4`。
- 本次发布提交：`9ef9a03ae284dc6067a85de904f838aea135a724`，已推送 `origin/main`。包含 `1f97679`、`f00ab23`、`57b6713`、`8c29f54`、`7deca17`、`99719f1` 等提交。
- `9ef9a03` 将此前尚未提交的系统收敛生产验收报告一并归档；私有数据、旧的未跟踪研究资料和测试缓存没有批量加入 Git。
- 发布候选来自该提交的独立干净工作树，生产构建 manifest 的 `gitDirty=false`。源码归档、构建归档、独立 canonical bundle 都有 SHA256 校验。
- 发布目录：`full-release-20261112-9ef9a03`。2026-11-11 **21:40:46 UTC**（北京时间 **2026-11-12 05:40:46**）返回 `DEPLOY_OK`。

本文件是部署后的文档记录，后续文档提交不改变实际运行构建的上述版本号。

## 发布前核验与控制

1. 逐项比较当前跟踪生产代码与服务器运行目录、源码检出。运行目录 package.json 仅缺旧版没有的 `db:migrate:postgres` 命令，依赖完全相同，差异可归因到历史提交；没有发现未解释的生产业务热补丁。
2. 核对四个 running Bot 均为 shared，两个共享组共四成员；四 QQ 登录及 coreReady 正常。确认 Agent/协议 PID 与创建时间、现有 ChildProcess 所有权，维护标记为空。
3. 通过现有 Agent 的发布操作互斥和维护所有权等待在途操作排空；维护期间由独立 heartbeat 保持监督。API 暂停后安装文件，再启动验证。未重启 Agent 容器或 QQ 协议。
4. 使用逐文件 CAS、制品哈希和旧文件回退材料安装 **1,533 个目标**，任何未知覆盖会拒绝；没有直接修改业务数据库内容或执行新的 PostgreSQL DDL，既有 schema012 保持。
5. 新 canonical bundle 完整刷新现有 Agent、ProcessHost、AI/shared/protocol adapter 的方法，保留其资源对象、数据库、租约、子进程和管线；包含 `shutdownOnce`。从同一 bundle 创建并持有新的自主、B站和收藏维护对象，验收后恢复运行，避免仅更新磁盘但仍运行旧闭包。
6. 两组 Worker 通过已有 `prepareAiStop(...allowLegacy:false)`、原子 StopFence 和 ProcessHost 的 PID/创建时间校验逐组重载。新 Worker ready 后才恢复调度。

服务器源码目录原有 **44 项文档/测试差异**与运行目录独立的 AGENTS.md 保留，未覆盖未理解的服务器资料。完整干净候选另存发布目录；生产执行代码同步完整。服务器部分克隆缺失 Git blob，通过已校验源码归档补齐 Git 对象并核对预期对象 ID，再同步 Git HEAD/index；未覆盖上述保留内容，也没有丢弃暂存修改（原暂存为空）。

## 实际验证结果

- 干净提交 `npm run build:production` 通过，包括 TypeScript、服务端、maintenance bundle 和前端构建；保留原有 chunk 大小提示。
- 发布控制离线验证通过：维护排他、慢 stop、独立 heartbeat、完整方法替换与回滚、三维护对象持有/恢复、QQ ChildProcess 身份、`shutdownOnce` 和同一 shutdown Promise。
- 服务器实际 Python/SDK 的 **16 文件、179 项测试全部通过**：提示词合同、本地原文恢复、索引迁移、Journal、合法批次、管理员检查点、历史退休、停机栅栏、发送合同、事实/关系和认知产品。
- 生产 API 恢复健康；预算内部路由无效身份返回401。没有真实 QQ/B站验收外发、重新登录、强制压缩、unknown 重放或付费模型测试。
- 发布后再次校验全部1,533目标；运行目录和源码目录各 **608 个当前生产源码文件**与候选逐字节一致，退役生产路径不存在。两处 build.json 的提交、sourceHashes、pythonSources、contracts、migrations 均通过实际文件校验。
- 两组重载回执确认 AI PID 均替换，成员分别 **3、1** ready；原子 StopFence 有效。Agent PID **2556741** 保持，四 QQ 协议 PID、创建时间及 ChildProcess 对象全部保持。
- 最终独立只读采样：**四 QQ online/coreReady 4/4**；两组 HTTP 成员数为3和1，均 ready、stopFence=true、idle=true、quiescing=false。Agent heartbeat 新鲜，maintenance=null。
- SQLite 四库 quick_check 全部 ok；废弃 `memory_search` 派生索引由 **4库存在 → 0库存在**；事实 **213 → 213**，事实内容摘要与 Journal 持久行摘要均与维护窗口前一致。没有迁移或改写已提交模型历史。
- 服务器源码 Git HEAD 与此次发布提交 `9ef9a03` 一致；GitHub `main` 的该次推送亦已核对。后续报告文档提交另行同步，不冒充新的运行构建。

旧清理报告中的“未部署/未生产迁移”是此前交付时状态，现由本记录续接为已部署。其披露的旧测试失败、历史凭据处置、长期自然认知质量等限制不因部署而自动关闭。期间有一次组健康采样遇到 ECONNREFUSED，时间落在该组正在重载的窗口；最终两组健康正常，不能把中间采样当作最终状态。

## 证据与剩余边界

私有证据保存在忽略目录 `.local/full-release-20261112/` 及对应服务器发布目录，包括 release.json、deploy-status.json、postrelease-verification.json、linux-validation-passed.json、组重载回执、storage-before/after 和 postflight-runtime.json。未将 Bot 身份、令牌、数据库、聊天正文或私有部署控制资料加入仓库。

Plan vs Actual：用户要求的全量已完成代码提交、推送与服务器部署完成；不是仅上传候选。必要偏移是为保留 QQ 所有权而刷新运行中方法/维护对象并使用原有栅栏重载 Worker，同时保留服务器未理解的文档/测试资料。未新增业务调度器、状态系统或对模型自主判断的限制。长期运行效果仍需自然流量观察，本次不宣称全部历史风险消除。
