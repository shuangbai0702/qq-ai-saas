# 平台机器人管理平台

实际工作区：`D:\MYCODE\APP\fishbot\qq-ai-saas`。React/TypeScript + Fastify + PostgreSQL SQL Migration + 单节点进程 Supervisor + 独立/共享 AstrBot。没有修改旧 Coopanion 或研究源码快照。

2026-10-29 更新：激活码注册、管理员发码、自定义 URL/Key 自动发现模型；默认每账号一个机器人，删除完成后才能替换，停止不释放名额。默认每 8 个成员共用一个受控 AstrBot。当前全量测试 74 项通过，新增发布安全检查及 Docker 构建入口；有加密凭据时拒绝用新密钥替代丢失的 Master Key。Docker 部署步骤见 [Docker 使用说明](docker/README.md)，实际验收边界与证据见 [综合记录](docs/windows-final-closeout.md)。

**当前可运行：Web、持久化、多租户权限、独立/共享 AstrBot、NapCat 网页扫码与会话恢复。已使用 `gpt-5.6-luna` 完成真实 QQ 私聊、多轮、显式事实记忆和主动发起聊天。两个授权 QQ 已加入同一个持久化共享组；两租户四逻辑 Bot 配置隔离、启停及恢复另有真实 AstrBot + 协议/模型替身测试。** 共享模式只支持平台受控能力，不是任意 Python 插件的安全沙箱。完整证据、未完成验收和 Plan vs Actual 见 [第三轮交付报告](docs/round3-delivery.md)。

最新整合见 [吸收各家机制与记忆增强](docs/behavior-synthesis.md)：加入话题相关召回、稳定资料/事件区分，以及可关闭的明确自述自动记录。仍按 Bot、群/私聊和 QQ 用户隔离，不是所有自然语言都能自动理解。

第二轮实现、数据、容量边界和 Plan vs Actual 见 [交付报告](docs/round2-delivery.md)。历史约662MiB固定成本包含重复计数，已纠正，不能继续引用作基准。

## 低内存生产启动（本地已验证）

```powershell
npm run build:production
$env:APP_ORIGIN='http://127.0.0.1:8787'
npm run start:production
# 另一个终端优雅停止
.\scripts\Stop-Production.ps1
```

前端与API统一入口 <http://127.0.0.1:8787>，无常驻 Vite/tsx。首次安装需要下面的 npm ci/setup。默认 compact PGlite 和精简 AstrBot；可配置 `DB_MEMORY_PROFILE=balanced`、`FISHBOT_ASTRBOT_SLIM=0` 回退。

真实 QQ：先 `npm run setup:napcat`（下载官方便携依赖，版本/SHA在安装记录），网页选择 NapCat 后点击获取二维码。Key只在网页配置；真实上线必须等待手机确认及协议端coreReady，模拟模式明确标识。详见 [QQ说明](docs/round2-qq.md)。

## Windows 启动

需要 Node.js 22+、Python、uv。本机已安装 Node 24、uv，以及 AstrBot Python 3.12 环境。

```powershell
cd D:\MYCODE\APP\fishbot\qq-ai-saas
npm ci
npm run setup:astrbot
# 可选：安装真实 Euler 实验环境
npm run setup:euler
Copy-Item .env.example .env # 仅首次且没有自己的 .env 时执行
.\scripts\Start-Dev.ps1
```

- Web：<http://127.0.0.1:5173>；API：<http://127.0.0.1:8787/v1/health>。
- 持邀请码注册新账号→创建机器人→选择大肥鱼/模型→选择**模拟协议**→测试二维码与确认→启动。AstrBot 是真实进程；Mock 二维码不能用手机 QQ 登录。已有账号直接登录，无需补填邀请码。
- 真实协议需要先完成 [运行适配说明](docs/runtime.md) 中的基础设施配置。缺少签名服务或 NapCat 安装会返回具体错误，平台不会自动改成 Mock。
- 停止：另一个 PowerShell 执行 `.\scripts\Stop-Dev.ps1`。它请求 API 优雅关闭实例和数据库。下次启动按持久化 desired state 恢复运行中的机器人。
- 编译生产单入口请使用上方 `build:production` / `start:production`，避免 TS 开发加载器。

本地持久数据在 `.local/`，默认 PGlite（真实 PostgreSQL WASM 引擎，不是独立 PostgreSQL 服务）。保留 `.local/master.key`，否则已保存的 Key 无法解密。`.local/`、`.env`、依赖和上游副本均被忽略。生产切换 `DATABASE_URL=postgresql://...`，SQL Migration 仍相同；独立 PostgreSQL 分支本轮尚未实机验收。

## 管理员

普通网页注册永远不授予管理员。停止 API 后通过本机命令创建：

```powershell
$env:BOOTSTRAP_ADMIN_PASSWORD='替换成至少12字符的密码'
npm run admin:create -- owner@example.com
Remove-Item Env:BOOTSTRAP_ADMIN_PASSWORD
```

登录该账号即可访问管理台。命令对已有邮箱会重置密码并授予管理员，只应由服务器维护者操作。

平台采用邀请制。管理员登录后进入侧栏「邀请码」，生成邀请码并设置使用次数与有效期，也可撤销尚未使用完的邀请。完整码仅在生成时显示一次，数据库只存摘要；列表与审计记录不保存完整码。撤销或过期不影响已经注册的账号。普通用户不能创建邀请码或访问管理员接口。

用户登录后只管理自己名下的机器人，可独立修改人格、System Prompt、模型 Base URL、API Key 与模型 ID。填写 URL 和 Key 后会自动读取供应商 `/models` 列表；已保存的 Key 可由服务端代为查询，浏览器不会读取明文。列表仅用于选择 ID，不代表模型推理、图片或工具能力已经验证；接口不支持列表时仍可手动填写。查询有超时、大小上限与切换页面/输入后的取消保护，不调用收费推理接口。

注册、登录、编辑配置和查询模型列表本身不会创建新的 AstrBot 进程。运行时默认加入每组最多 8 个成员的共享组，满组后再建组；需要任意自定义 Python 插件的机器人应使用独立模式。

## 验证

新建机器人默认使用共享运行组（每组默认 8 个，仅平台受控功能），可通过 `SHARED_GROUP_CAPACITY=1/2/4/8` 调整新组容量，也可显式选择独立实例。已有组按数据库持久化容量运行，改环境变量不会自动改旧组或迁移历史；已有机器人的运行模式不会随升级自动改变。有原生历史或自定义插件的独立实例，切换前需要另行迁移验证。

可选降低首次空库启动峰值：在构建机器执行 `npm run db:prepare-template`，将生成的整个 `resources/pglite-template/` 随部署文件一起分发。模板只含全新 PostgreSQL 空库，不含用户、密钥和业务表；初始化成本仍由构建机器承担。模板须与安装的 PGlite 版本一致，版本升级应输出到新目录（命令可传目标目录），再通过 `PGLITE_TEMPLATE_DIR` 指定。没有模板时沿用常规初始化；已有 `DATA_ROOT/postgres` 永远不会被替换。生产运行不应现场生成模板。

```powershell
npm run build
npm test
# 先启动开发服务；首次需要浏览器包
npx playwright install chromium
# 从管理员页面生成一个可用测试邀请；不要把真实码提交到仓库
$env:TEST_INVITE_CODE='本次测试的邀请码'
npm run test:web
Remove-Item Env:TEST_INVITE_CODE
# 邀请制及模型发现完整页面回归：独立临时数据库，无需真实邀请/Key
npx tsx tests/invitation-model-browser.ts
references/euler/.venv/Scripts/python.exe experiments/protocol-lab/verify_euler.py
references/astrbot/.venv/Scripts/python.exe scripts/sample_resources.py --seconds 30
```

`npm test` 会启动真实 AstrBot，需要先安装其环境；使用独立测试数据目录，不使用演示用户的机器人。`test:web` 创建新的测试用户和机器人；为避免重复 QQ 绑定，测试生成独立模拟 QQ 号。截图与原始结果在 `test-results/`。

## 代码边界

社交学习与配图基础版已接入：配置页可开启群体表达学习、语境选图，并上传本机器人专属 PNG。真实 `gpt-5.6-luna → QQ 图片` 已验收；自动收集/识别群友图片尚未启用。范围、限额、测试和失败记录见 [社交能力实施交付](docs/social-integration.md)。

| 目录 | 职责 |
|---|---|
| apps/web | 中文管理台、注册、向导、配置、QR/SSE、日志、管理员 |
| apps/control-api | 会话、租户授权、配置版本、密钥、命令与审计 |
| apps/node-agent | 单节点命令执行、进程所有权、健康、有限退避恢复 |
| packages/contracts | 当前必要的 AI/Protocol 接口、配置与人格模板 |
| packages/database | pg/PGlite 双驱动、事务和真实 SQL Migration |
| adapters/astrbot | 独立 root、配置生成、原版 AstrBot 无面板启动器 |
| adapters/protocol | Euler/hiro 状态桥及 NapCat 私有管理 API 封装 |
| adapters/mock | 明确模拟的 OneBot 端；仍与真实 AstrBot 通信 |
| references | 安装脚本生成的上游独立副本，含版本记录 |
| experiments/protocol-lab | 源码契约检查；不属于正式协议实现 |

当前进程/目录隔离不是恶意插件的安全沙箱。未开放任意插件安装。Linux cgroup 限制实现与 systemd 模板尚未在 Linux 实机验证；正式收客户前需完成独立 OS 身份/目录权限、真实 QQ 和付费模型验收。见 [验收与 Plan vs Actual](docs/delivery.md)、[下一阶段](docs/next-iteration.md)。


能力统一管理入口：机器人 → 基本配置 → 能力管理。支持事实纠正/遗忘、表达停用/清理、图库场景编辑/启停、主动会话暂停和人物状态重置。实际范围及测试见 [能力管理交付](docs/capability-management-delivery.md)。自动收图识别仍未接入。
