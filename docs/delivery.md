# 第一轮交付与独立 Plan vs Actual 复核

日期：2026-10-28，Windows 开发机。实际产品根 `D:\MYCODE\APP\fishbot\qq-ai-saas`。依据开发前 `implementation-plan.md` 和用户本轮17节任务复核；没有改写原计划来隐藏范围缺口。

## 已交付行为与证据

| 用户要求 / 原计划 | 实际实现 | 验收与状态 |
|---|---|---|
| 独立产品工作区、保护旧代码 | 新目录；安装引用均在 references 副本 | 未修改旧 Coopanion/研究快照/无关Git |
| 账号注册登录登出、密码会话 | scrypt、随机会话cookie、DB哈希会话、HttpOnly/SameSite、限流 | api.test 与浏览器真实API通过；未做找回密码/邮箱验证 |
| 租户、成员、机器人CRUD | 全部要求的表及sessions；SQL Migration；按tenant授权 | 两用户越权读取/操作/日志/QR/SSE被拒绝；本轮UI仅个人租户，不含邀请成员 |
| 配置、人格、模型Key | 不可变revision、CAS、AES256-GCM+tenant/bot AAD、运行环境引用 | 加密/作用域/409/隔离通过；浏览器轮询不改变编辑基准版本 |
| 中文Web完整操作 | 登录、注册、列表、向导、配置、QR/SSE、日志、管理台 | Playwright真实浏览器通过；桌面及390px无横向溢出 |
| AI Adapter、独立AstrBot | 原版4.29.3 lifecycle；共享代码venv，独立root/tmp/data/db/persona/token/ports | 四个真实Python进程；Persona DB、Provider配置、实际ready回执、启停/重启通过 |
| AI真正处理消息 | WebSocket/OneBot进入真实AstrBot，真实provider SDK调用接口、发send action | pipeline.test通过；QQ与模型端均为接口fixture，不是手机QQ或外部付费模型验收 |
| 大肥鱼风格可用人格 | contracts中的原创风格模板、短句/群友QQ ID/反助手腔、可编辑、私群隔离、@触发、主动2% | Persona注入、上下文私群分离、白名单、未@不触发通过；无复杂关系/情绪状态机 |
| 真实QQ网页扫码 | ProtocolAdapter、NapCat管理API、Euler/hiroPNG/状态桥、私有代理、SSE | **仅模拟闭环通过；真实扫码未通过**。NapCat安装runner缺代码，Euler缺兼容签名环境/真实账户 |
| QR过期取消刷新恢复 | 持久login_sessions、generation/terminal保护、唯一绑定 | 模拟QR、等待确认、绑定、过期、刷新、取消、迟到在线、重复QQ拒绝通过 |
| 生命周期真实控制 | PID所有权检查、实际进程树启停、desired/observed、generation、持久命令、幂等 | 四实例、停止不影响其他、真实崩溃恢复、整个app关闭重开恢复通过 |
| 自动恢复与资源限制 | 每Agent最多3次连续恢复/退避；Linux可选cgroup；Windows RSS统计 | 恢复预算/旧取消安全测试通过；cgroup未Linux实测，Windows没有硬配额 |
| 管理员与服务器概况 | 本地CLI创建管理员、资源/异常/进程表、真实QQ计数排除Mock | 管理员浏览器测试通过；普通注册无提权路径 |
| 资源与生产容量 | Windows各进程树RSS、Linux可用采样脚本 | 有空闲数据；无真实QQ/媒体/峰值/长期压测，不能批准生产容量 |
| Linux运行、独立DB | systemd模板、pg驱动、可选独立Agent入口 | 仅源码/构建验证。无Linux、独立PostgreSQL、独立Agent组合实际部署 |
| 记忆/插件/套餐管理 | 复用AstrBot会话与数据库、禁止普通用户安装任意插件 | 独立记忆基础已具备；记忆CRUD、插件配置UI、额度/到期/套餐页面未实现；支付按要求不开发 |

## 运行状态

- 开发Web `http://127.0.0.1:5173`，API `http://127.0.0.1:8787/v1/health`；本轮结束留在运行状态。
- 数据库：持久PGlite PostgreSQL WASM，与API/Agent同一进程，**不是独立PostgreSQL服务**。`pg` 分支尚未实机验收。
- AstrBot：安装真实依赖，多实例已通过；开发服务保留浏览器验收机器人。它们无外部Key，不能向用户保证模型调用可用。
- QQ：Mock Node进程在运行；Euler依赖安装及源码契约通过但未登录；NapCat仅客户端适配，未安装真实运行端；签名服务未运行。
- 当前只绑定127.0.0.1，没有生产部署。管理员本机演示信息在 `.local/demo-access.txt`，未写入产品源码。

## 测试证据索引

- `test-results/backend-tests.log`：最终 `npm test` **16 pass / 0 fail**，包含8个API子项与7个顶层专项（Node计数含父测试）。四真实AstrBot及app恢复测试约58秒，总约85秒。
- `test-results/browser-tests.log`、`browser-results.json`：注册/向导/真实API/模拟QR/状态/刷新持久化/配置/真实AstrBot ready/移动布局/登出登录通过。
- `admin-browser-results.json`：管理员实际页面、真实资源API、编辑器跨轮询保留基准版本、409不覆盖新配置通过。
- `browser-qr.png`、`browser-config.png`、`browser-dashboard.png`、`browser-mobile.png`、`browser-admin.png`：真实浏览器截图；桌面主页面已人工查看，不是静态原型。
- `pipeline-results.json`：真实AstrBot→模型fixture→发送消息两次；Provider Key、Persona、上下文、群白名单、@规则。
- `euler-source-contract.json`：真实安装的Euler/hiro导入、connector schema、方法签名；不等于登录通过。
- `runtime-measurements.json`：四空闲AI+Mock原始RSS；`lifecycle-final.json`：故障注入后状态；`live-resource-samples.json`：开发API/Web样本。
- `build.log`：TypeScript与生产构建通过；Rollup提示上游Zod纯函数注解位置问题（构建成功）。`production.test.ts`验证构建静态文件和API单服务。
- `npm-audit.json`：本轮执行生产npm依赖审计0漏洞；它不代表全部Python依赖或产品无安全缺陷。

## 失败记录与处理

1. 早期runtime测试没有创建`.local`，失败于目录不存在；创建父目录后复测。
2. AstrBot重写JSON会带BOM；测试读取实际上游文件时去BOM，未降低配置值断言。
3. Euler源码契约检查发现`login`在Client而非BaseClient；按实际源码修正检查，register桥也改为Client.register；尚未真实登录，不能推导桥已全兼容。
4. 浏览器重复运行最初使用固定模拟QQ，被平台唯一绑定保护拒绝，等待“运行中”超时；`browser-repeat-fixed-qq-failure.log`保留。修复测试账号隔离，不放宽重复QQ保护；新模拟QQ复测通过。
5. 单入口静态目录复核时误把Vite相对输出当项目根dist；文件检查发现后恢复`apps/web/dist`，新增真正静态资产请求验收。
6. 最初依赖审计提示旧static间接依赖风险，升级到`@fastify/static 10.1.8`，最终审计0；没有屏蔽审计。

## 合理偏移与未收口

- PGlite替代本地独立PostgreSQL、默认嵌入Agent：减少Windows环境阻塞，保留真实PostgreSQL SQL和pg驱动；生产DB、独立Agent尚未验收。
- 使用React+自定义统一CSS而非Tailwind/shadcn；页面有可用的真实交互，未为了库名重做UI。
- 不开放任意第三方插件安装、不过早做多节点：当前OS同身份进程无法对恶意插件提供安全隔离。
- 真实QQ失败的原因不全是外部凭据：**NapCat runner/自动安装仍是待开发产品功能**；Euler签名基础设施仍未提供，不能只让用户扫码就宣布完成。
- Linux硬隔离、Windows硬配额、媒体峰值、长期在线、实际供应商错误诊断、记忆/插件/用量页面均未收口。已有mock不能替代这些验收。

因此本轮交付为“可运行Web控制平台 + 经真实进程和消息链路验证的AstrBot集成 + 真实协议适配代码/实验入口”，不是“已能正式托管四个真实QQ客户”。下一轮具体范围见 `next-iteration.md`。
