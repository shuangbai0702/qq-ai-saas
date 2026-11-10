# Windows 生产监督器接入

`scripts/windows-production-supervisor.py` 用私有 JSON 配置启动独立 PostgreSQL、API 和 Agent，并托管内存 guard/controller、可选临时 Cloudflare Tunnel。配置、环境文件、数据库、授权文件和日志均放源码仓库外；源码仓库不保存真实密钥。

```json
{
  "sourceRoot": "D:/FishBot/source",
  "dataRoot": "D:/FishBot/private/runtime",
  "stateRoot": "D:/FishBot/private/control",
  "node": "D:/FishBot/tools/node/node.exe",
  "python": "D:/FishBot/tools/python/python.exe",
  "postgresBin": "D:/FishBot/tools/postgres/bin",
  "postgresData": "D:/FishBot/private/postgres",
  "executionAuthority": "D:/FishBot/private/execution-authority",
  "authorityId": "windows-target",
  "cloudflared": "D:/FishBot/tools/cloudflared.exe",
  "dnsSyncConfig": "D:/FishBot/private/txt-sync.json",
  "readOnlyRoots": ["D:/FishBot/tools/python-base"],
  "environment": {
    "PGHOST": "127.0.0.1",
    "PGUSER": "fishbot",
    "PGDATABASE": "fishbot",
    "PGPASSWORD_FILE": "D:/FishBot/private/database-password",
    "PORT": "8787",
    "PGPORT": "5432",
    "ASTRBOT_SOURCE": "D:/FishBot/source/references/astrbot",
    "ASTRBOT_PYTHON": "D:/FishBot/tools/astrbot-python/python.exe"
  }
}
```

路径必须为绝对路径。PostgreSQL 数据目录须已初始化/恢复，源码须已构建；监督器不执行数据库导入、schema迁移或依赖安装。`environment` 仅接受字符串；由部署写入需要的 PostgreSQL 凭据、应用配置和协议路径。监督器强制 `DATA_ROOT`、`EXTERNAL_AGENT=true`、`FISHBOT_MEMORY_POLICY=off`、`NODE_ENV=production`，API/Agent 固定 `--max-old-space-size=128`。`cloudflared` 可省略，启用时固定转发 `http://127.0.0.1:8787`，所以启用 tunnel 应保持端口 8787。

先运行无 QQ 预验收：

```powershell
python -B scripts/windows-production-supervisor.py --config D:/FishBot/private/config.json --preflight
```

`--preflight` 无条件禁止 Agent 与 tunnel，即使授权文件匹配也不启动；API 禁用嵌入式 Agent，故不会因 API 启动拉起 QQ。三基础服务合同尚缺 Agent 时内存 guard 保持回收暂停，这是预期状态。依次启动 PG（同时核验 `pg_isready` 与本数据目录 `postmaster.pid` 属于实际子句柄）→API（健康返回 `ok=true,agent=false`）→Agent（仅正式模式且授权文件去掉首尾空白后等于 `authorityId`）→guard/controller。Agent 运行期间授权不再匹配则停止其整个 Job 树；不依赖仅启动时检查授权。

预验收结束，向 `stateRoot/supervisor.stop` 写 `supervisor.json` 中的 PID；等待 `supervisor-stopped.json` 的同一 PID 且 `treesExited=true`，并核验进程出生时间、QQ子树退出。不要仅凭陈旧的 stopped 文件判断本次停止。再完成源端停机/唯一执行权交接，发布授权文件，并以不带 `--preflight` 的同配置启动。停止文件只匹配当前监督器 PID，不使用 HTTP 控制口。

用管理员 PowerShell 安装低权限开机任务：

```powershell
./scripts/install-windows-production-task.ps1 -Config D:/FishBot/private/config.json -Preflight -Start
```

默认 `-ServiceAccount NetworkService`（SID `S-1-5-20`），也支持 `LocalService`（`S-1-5-19`），无需创建用户或保存账户密码。默认身份的[本地权限较低](https://learn.microsoft.com/en-us/windows/win32/services/networkservice-account)。PostgreSQL Windows [要求非管理员服务账户](https://wiki.postgresql.org/wiki/Running_%26_Installing_PostgreSQL_On_Native_Windows)，不能继续使用之前的 SYSTEM 任务直接运行 `postgres.exe`。先停止旧监督器并确认树退出，再重新注册；修改任务定义不会替换正在运行的旧实例。直接从提升权限的管理员终端运行监督器也不绕过这一 PostgreSQL 限制。

安装器对源码（含 node_modules/references）、Node/Python/PostgreSQL 工具根增量授予该服务 SID 的 RX；对 dataRoot/stateRoot/postgresData 授予 M；对私有配置、密码文件授予 R，cloudflared 可执行文件授予 RX；授权文件父目录授予继承 RX，确保管理员后续原子替换文件仍可读取。保留其他账户既有 ACL，不新增 Everyone/Users 授权，拒绝对整个盘根授予权限。安装前可加 `-PlanOnly` 输出不含环境值的 ACL/任务计划，不执行 ACL 或任务注册；实际路径必须部署到位。若另有 `NAPCAT_RUNTIME_DIR` 或不在上述目录内的 Python 基础运行库，部署方应核对其确切读写合同并补足相应目录权限，不能直接扩成全盘可写。

UV/venv 的 `Scripts/python.exe` 可能仅为 trampoline，真实解释器位于 `sys.base_prefix`，不能只授权 venv。用配置中实际 Python 的 `-c "import sys; print(sys.base_prefix)"` 查清真实基目录，再把它列入私有配置 `readOnlyRoots`。安装器用配置 Python 的 `-I -S` 调用 `os.path.realpath` 解析每个显式根的 Junction/符号链接，随后对真实目标 `/T` 授予 RX，覆盖已存在且不继承的文件；仅给父目录加继承 ACE，或对 Junction 自身递归 ACL，都不能证明实际版本目录的 `python.exe` 可访问。示例的 python-base 必须替换成现场实际根，或在非 venv 部署中移除该示例项。全部 RX 目录先处理，全部 M 目录后处理，避免对共同祖先的递归 RX 最后覆盖 runtime/control/PG 的写权限；不对整个 Admin 用户目录授权。

正式启用时先按上述停止文件停完预验收监督器，再用同任务名重新注册、不加 `-Preflight`。`-Start` 可省略，仅注册启动任务。任务设置：NetworkService/ServiceAccount/Limited、开机触发、IgnoreNew、StartWhenAvailable、无运行时限、失败后每分钟重启最多 999 次。任务调度器的重启间隔[最小为 1 分钟](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-restartinterval)；内部 PG/API/guard 使用 1 秒、Agent/controller/tunnel 使用 10 秒退出退避。监督器文件锁保证手工运行与调度器实例也不会同时接管。

监督器将自身加入持有 kill-on-close 的主 Windows Job；每服务创建嵌套 Job，以 `CREATE_SUSPENDED` 启动，绑定后才恢复线程。Job 创建/绑定失败时不执行该服务，尤其不能降级为未受管 Agent。真实根进程句柄退出后，先终止并确认整个服务 Job 的 `ActiveProcesses=0`，再开始退避和重启，不遗留旧 QQ/媒体子进程。监督器异常退出会关闭主 Job 句柄，由 Windows 终止全部子树；正常停止依次结束受管服务树，对 PG 执行 `pg_ctl -m fast -w` 后确认退出，最后写 stopped 证据并解除主 Job 关闭杀进程标志，正常退出不触发失败重启。

三基础服务原子记录位于 `stateRoot/services/{control-api,agent,postgres}.process.json`：`{pid,created,root}`，出生时间来自 `psutil`，API/Agent 的绝对入口受源码根归属保护，PG 的 `-D` 为私有数据目录。内存管理合同见 [内存接入说明](windows-runtime-memory-policy-20261110.md)。不要删除或伪造出生时间字段。

每服务输出独立捕获到私有 `stateRoot/logs/*.log`，超过 2 MiB 滚动保留一份。环境中常见密钥/密码/Token/Cookie/授权值、数据库 URL 与解析密码在写入前脱敏，超长输出整行舍弃。监督器错误只打印异常类型；不打印配置、环境或完整命令。不得把这些私有原始日志提交仓库。

已在隔离 Windows 进程验证 4 项：正式/预验收授权门控；服务根进程退出后孙进程被清理、真实出生时间记录更新及凭据脱敏；强杀监督器替身后主/嵌套 Job 清理全部子树；安装器 PlanOnly 默认 NetworkService/Limited、可选 LocalService、拒绝 SYSTEM且不输出环境秘密。未在本机安装计划任务、执行 ACL 修改、启动真实 PG/API/Agent、操作生产数据或制造真实 OOM；目标机仍须实际预验收并确认低权限服务身份的目录访问与本机 Windows Job 嵌套能力。

## 既有 Worker / DuckDNS TXT 接线

公网 Worker 通过 DuckDNS TXT 读取 QuickTunnel URL 时，使用可选 `dnsSyncConfig`，不创建 named tunnel 或新域名。其私有 JSON 示例：

```json
{
  "tokenFile": "D:/FishBot/private/duckdns-token",
  "domain": "shuangbai",
  "logFile": "D:/FishBot/private/control/logs/cloudflared.log",
  "authorityFile": "D:/FishBot/private/authority/owner",
  "authorityValue": "windows-target",
  "routeEnabledFile": "D:/FishBot/private/authority/route-enabled",
  "healthUrl": "http://127.0.0.1:8787/v1/health",
  "stateFile": "D:/FishBot/private/control/txt-sync-state.json"
}
```

`authorityFile`/`authorityValue` 应与监督器执行权合同一致；切换前 `routeEnabledFile` 必须保持不存在或不匹配。正式模式只有执行权匹配、API 健康且 cloudflared 已启动才托管 TXT 同步进程，退出后 10 秒重启；预验收绝不启动该进程。执行权撤销时监督器停止同步进程；脚本自身还要求两个 gate 文件内容都匹配且本地 API `ok=true`，在健康请求返回与真正发布前重查 gate。

脚本每 60 秒读取当前日志最后 2 MiB 内最新完整合法 `https://<lowercase-label>.trycloudflare.com` 根 URL；拒绝 HTTP、外域后缀、用户信息、端口或路径混入。固定向 [DuckDNS TXT 接口](https://www.duckdns.org/spec.jsp) 发送 HTTPS `domains/token/txt` 查询，不允许重定向；仅返回 HTTP 200 和 `OK` 后原子记录成功。重启时成功 state 的 domain/URL/authority 相同则不重复发布；KO/网络异常不记成功，下一周期重试。日志只输出固定决策，不输出 Token、请求地址或原始异常。

安装器读取 DNS 私有配置只取得路径：对配置与 Token 文件授予 R，对 gate 父目录授予继承 RX，对同步 state 的父目录授予 M。gate 建议独立放在 `private/authority`，避免给整个 private 根反复递归设 ACL。监督器与该脚本不自动停止源端 timer，也不创建路由 gate；切换时主执行方先停并确认旧 `cf-txt-sync.timer/service`，完成唯一执行权交接后才发布 route gate。已发出的外部请求不能因事后撤权撤销，切流前必须先停止旧写入者。

新增 4 项无外部写入测试覆盖：双 gate/本地健康/健康返回后撤权、KO 不记成功与 OK 后重启去重、新 URL 更新、严格 URL 与固定 DuckDNS/health 端点。未执行真实 DuckDNS 更新、未读取生产 Token、未操作源端 timer 或公网路由。
