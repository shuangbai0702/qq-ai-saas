# 会话恢复启动与扫码状态（2026-10-31）

用户追加要求：老用户启动优先恢复QQ会话，不能恢复就引导扫码；不得打断在线QQ。临时扫码故障优先排查，CPU无需极限压缩，每QQ平均3%–6%可接受。

## 修改

- `apps/control-api/src/app.ts`：NapCat普通start建立同代登录观察会话，有效期180秒；不发送强制QR的login命令，现有Agent先走原生`-q`和一次快速登录重试。恢复成功由现有可信QQ身份绑定流程确认；恢复失败时显示同进程二维码。正在启动/登录/已在线的重复start复用当前生命周期。
- `apps/web/src/bot-detail.tsx`：启动后进入QQ页（有未保存编辑时保留页面）；按generation重建连接面板，防止继续展示上次登录记录。
- `apps/web/src/qq-panel.tsx`：明确提示自动恢复、待扫码、已扫码等待手机确认、初始化；扫码后隐藏旧二维码，刷新前说明会中断当前尝试。沿用页面取消、请求序号、会话ID/sequence门禁。
- 既有Agent、协议适配器与原生Session文件不改；未新增兼容层、数据库表或本地常驻进程。

## 真实故障与证据

故障账号19:26:30原生协议已报告已扫码，之后没有登录成功回执，19:28到期清退；更早一次收到QQ服务端错误168（账号安全限制），不能仅凭该历史错误断言本次原因。该用户表示已确认且手机无报错。

19:36新尝试中，原生协议收到扫码，随后登录会话变online，协议健康也online；当前3个真实QQ均在线。恢复发生在本轮Web/API部署之前，不能归功于新UI或声称定位并修复了QQ原生握手根因。平台展示和启动流程缺陷已单独修复。

监控未知问题随后真实API采集完整，onlineQQ=3；未把未知强改为0或用配置数冒充在线数。历史短暂采集不全的具体原因未捕获，不宣称已根治。

## 验收与部署

- Windows构建通过；Linux隔离容器生产构建通过。
- Linux15项针对性回归全部通过：普通resume、同进程QR回退、重复start、已在线start、账号校验、租户隔离、迟到online与新stop交错、旧登录取消/超时、新登录与清退互斥、共享成员隔离、Session/Key保留。
- Windows Playwright验证SSE不可用时轮询仍显示二维码，反复刷新、已扫码隐藏旧码、拒绝刷新保留原会话、卸载停止轮询；故意断开SSE产生预期ERR_FAILED，不是应用JS异常。
- Linux实际owner账号重复start返回reused=true，generation不变；未为了验证免扫码强制注销任何在线QQ。真实旧Session重新登录成功路径以隔离受控测试及已有适配器测试为证，未做生产强制停机恢复演练。
- 仅替换API编译文件和Web静态产物，独立重启API；部署前后Agent、3个NapCat、共享AI共5个PID/创建时间不变。账号、配置、Key、QQ Session、记忆未删除。备份及回退在Linux `/opt/fishbot-resume-20261031/backup`。
- Windows/Linux源码按SHA256核对；Web JS摘要 `a9cd1ea07b06e06f8128d1c45ef52df47cbbfad3d38d4d67d3d74fdfeac67fde`，API JS摘要 `7c14249cf8890a82c4747da4ffad8d757d8aa2f5d9f0e86f13c09fe5dfa9a2ac`。

## Plan vs Actual

追加启动优化已实施并独立部署；并发与持久数据保留做隔离验证；生产在线start做真实验证。扫码登录在新部署前恢复，原因仍有外部协议不确定性；不声称修复了未定位的原生问题。为保护在线业务未强制停启QQ，CPU高峰的回归容器为有限任务，完成后已退出。未改变48MiB/40秒策略或Guard；API短时更新允许Guard按原规则临时放宽，等待其自行恢复，不手工绕过保护。

最终安静窗口30秒：整机CPU平均9.0%，CPU PSI some avg10从1.04降到0.93，RAM已用750.3MiB（3个QQ在线，不能与1个QQ时的旧基线直接对比）。此前5秒逐进程采样：最高QQ约单核6%，其余QQ约0.2%–0.4%，共享AI约0.2%；不是长期平均承诺。Linux回归/构建容器已退出，未增加CPU硬限制。API更新触发Guard按既有规则临时放宽，随后自动恢复，最终实读NapCat/共享AI high=50331648，独立AI=16777216，API/PG仍134217728。zswap A/B单次unit已inactive且Result=success。
