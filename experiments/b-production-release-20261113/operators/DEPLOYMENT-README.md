# B 增量部署驱动（离线准备，尚未执行）

由 `.local/architecture-release-20261113` 的成熟 hold/fence/reload/API 驱动适配。当前只进行了脚本语法检查、本地维护入口回归；没有执行远程 hold、安装、维护替换或重启。根智能体需完成实际回归与媒体验收并 review 以下脚本后才执行。

## 输入与步骤

先协调 SSH 独占窗口，然后在仓库根目录显式执行：

```powershell
python .local/b-release-20261113/remote.py stage-operators
python .local/b-release-20261113/remote.py baseline
python .local/b-release-20261113/prepare-release.py <已审核的干净构建目录> <显式targets.json>
python .local/b-release-20261113/remote.py stage
```

`baseline` 从当前 Agent 捕获服务记录、真实运行成员、generation/revision、QQ protocol 子进程对象与创建时间、AI worker 身份，以及 Python 源码摘要和媒体配置当前摘要。不复用以前发布的 baseline。hold 拒绝超过 15 分钟的 baseline，并再次核对实际成员和协议所有者。准备 manifest 后才能生成 `deployment-gate.json`：

```json
{"releaseAuthorized":true,"regressionClosed":true,"mediaClosed":true,"manifestSha256":"实际manifest.json的SHA256"}
```

门禁是根智能体的已审核结果记录；不是新增用户确认。不要在验收尚未完成时填写为 true。所有路径限定本次 `.local/b-release-20261113`，现有媒体测试文件不覆盖。

随后才可以运行 `python .local/b-release-20261113/deploy.py`。顺序为 `hold → check → backup-ready → backup → install → hotmaintenance → reload → attest → restart-api → verify → resume → status`。

## 所有权与回退

- `hold` 关闭三个后台 owner，等待控制/Work/媒体 drain，并获取 AI 原子停机 fence。Agent、Postgres、QQ protocol PID 不重启。
- `hotmaintenance` 使用新 bundled `maintenance.js` 的摘要导入，依次刷新 autonomy、Bilibili、favorite。autonomy 使用 `{start:false}`，三个替换 owner 全部保持 closed/stopped 到 resume。Bilibili recover 只在 drain 后运行，沿原账本核对未完责任，不重放外部发送。
- 安装期间保留 Agent 原 interval 对象，其 tick 被本次 hold 的 no-op 遮蔽。resume 恢复原 tick property descriptor（原来继承则删除遮蔽属性）、停止临时 heartbeat writer，并恢复原后台运行状态。
- rollback 先 drain 新 owner，再恢复旧对象、context/health/start/apply/attach 方法及原 maintenance markers。已变更 AI worker 通过现有 ProcessHost 重载恢复的 Python；最后 ready 检查通过才 resume，不新建/重放 Work。
- 文件覆盖记录精确 before/after 摘要，备份原字节。install 中途失败也会尝试 restore，已有其他修改的文件会拒绝覆盖并留待人工处理。
- 媒体候选摘要固定为 `75bc295c63101164dbd7b85c1161de5aa3d5507d4b8c7fe282c218cc9e6017c2`。`cfg.dataRoot/media-resources.json` 在 baseline 和 install 前都核对摘要；原本不存在时 rollback 只在当前仍是本候选摘要时删除新增文件，原本存在则恢复备份。
- API 仅按服务 PID/创建时间和 sourceRoot 身份终止，由现有 supervisor 拉起。重复/回退 API reload 接受的是本次之前证明的完整新服务记录，不仅比较 PID；拒绝旧 proof 的时间戳。

## Review 重点与未执行项

请审核 targets 覆盖所有变更源码、Python、新编译模块、maintenance bundle、Web 构建与 build.json，不能遗漏被运行时 ESM 缓存的数据库/工具闭包。manifest 的未覆盖源码会与构建摘要比较，发现 drift 则停止。

未进行真实部署演练、远程 baseline 或在线回退。若控制面 API 重启在身份证明生成前失败，驱动会保守停止并报告 `ROLLBACK_NEEDS_OPERATOR`；不要绕过身份核对终止其他进程。部署后仍需验证实际 Provider 参数/运行时加载证据、业务责任延续和协议 PID/创建时间，不能以 source hash 或 API health 代替业务验收。


## 强制业务状态备份（安装门禁）

`backup-ready` 由仍被持有的 Agent 核对全部 fence、原QQ所有者和成员，并产生本次唯一 permit。`backup-release.ps1` 以SYSTEM执行 `backup-release.py`，先建立仅SYSTEM/Administrators/执行SID可读的保护目录，继而：

- `backup-pg.mjs` 保持 `REPEATABLE READ READ ONLY` 导出snapshot，pg_dump custom使用该snapshot；表计数来自同snapshot。完整dump只恢复到本次随机新数据库，核对所有public表计数，关闭连接、核对DB所有权后删除并复查。不会向生产schema恢复。
- SQLite源以只读模式通过backup API生成独立behavior/主Journal副本，再通过backup API恢复到另一个独立SQLite文件，检查integrity_check和所有表计数。不复制WAL碎片，不改变/清理Journal。
- 从baseline列出的现存bot ID限定路径；NapCat实际路径必须从各自napcat-platform.json确认，不能猜其他QQ目录。使用本次新建VSS卷快照复制`dataDir/profile`、`home`、`napcat/config`及identity.json，逐文件核对hash。只有本次snapshot GUID会在finally删除，创建receipt保存在保护目录。QQ/NapCat始终不停机、不清缓存、不重新登录。此份备份是磁盘crash-consistent，不声称验证QQ账号可重新登录。
- master.key与部署私密配置一并保留在保护目录，不进入git/普通日志。逐库计数和逐文件hash清单也留在该保护目录；控制面只输出完成标记、总数、摘要和保护目录位置。

`install` 必须匹配本次permit、Agent PID、baseline时间、完整备份manifest哈希与成功restore/ACL/VSS清理标记；任何备份失败都禁止install。若备份任务仍运行，driver保持held并报告人工处理，不边备份边继续发布。若备份已终止，失败路径只恢复程序/owner并resume。

业务数据库与QQ状态备份**不被程序rollback自动还原**：真实新消息可能已到达，盲目恢复会丢数据。需要数据恢复时必须另行根据后续消息、业务责任和备份时点制定恢复步骤。此脚本仅已做离线语法检查，没有执行真实生产dump/VSS/restore；首次实际backup必须逐项成功才能发布，不以未执行验证冒充通过。
