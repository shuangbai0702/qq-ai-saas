# 第三轮关键路径审计与修复

范围：现有 API → Agent → ProcessHost → NapCat / SharedAstrBotAdapter → Worker → 原生 AstrBot SDK/OneBot。不是全仓安全认证。按 plan-execution-review 和 async-lifecycle-audit 检查提交代次、资源所有者和清理路径。

| 问题与根因 | 当前修复及位置 | 验证 |
|---|---|---|
| 配置/命令迟到回执可能覆盖新状态 | `apps/node-agent/src/agent.ts` 按 generation/revision 提交；已恢复且就绪的组成员命令 supersede，避免第二次重启 | shared-product、shared-recovery 及真实恢复 |
| 共享组只在实验脚本，重启不能重建成员 | Migration002 + `adapters/astrbot/shared.ts` 持久化组/成员，串行分配、容量溢出新组，组崩溃恢复全部 desired-running 成员 | allocation / product，四逻辑 Bot，Agent 重启 |
| 删除/暂停一个成员可能留下连接/Provider | Worker 先 stop_platform 排空事件，再清理 Provider、Persona、路由、主动状态；成员墓碑拒绝旧 generation，合法同代重试可幂等 | 独立停止/删除/改配置/重启测试 |
| Key 进入共享配置文件/进程环境的风险 | Key 仅走鉴权 loopback IPC，内存创建 Provider；磁盘仅引用。所有 Provider/Persona/UMO 有 Bot 作用域 | 两租户四 Bot 不同 Key/Persona/同群号路由 + 磁盘无明文测试 |
| 旧 QR 过期任务可取消后来 QR | 过期取消命令绑定 login session ID，执行前校验当前最新会话 | 后端登录过期/刷新回归 |
| QQ quick login 早于网络就绪 | NapCat 等待扫码状态下仅按当前 PID 重试一次已保存匹配身份；显式 QR 不走自动恢复 | 曾失败的真实记录保留，受控恢复及后续重启 |
| PID 重用 / 旧 close 误清新进程 | `process-host.ts` + `scripts/process_control.py` 校验创建时间/路径，close 必须仍是同一 child owner | 进程测试；旧 manifest 无创建时间的兼容路径需运行重建 |
| 跨 pipe chunk 的密钥绕过脱敏，日志队列无界 | 按 UTF-8 行缓存后统一脱敏；超长行丢弃，最多 32 次待写、2MiB 轮转 | process-host 合成密钥跨块回归 |
| 停止后迟到模型继续发言 | behavior 按配置 owner/运行代次校验；配置已解除时禁止 native fallback；每段重检 | fixture SDK / OneBot 生命周期测试 |
| 页面换 Bot 后旧组件回写 | `apps/web/src/main.tsx` 按 Bot ID key，QQ 当前在线来自 health，旧成功扫码记录不代表当前在线 | 浏览器真实 API 操作及构建检查；并未穷举所有网络交错 |
| 上游把凭据反射进模型探测结果 | `packages/shared/model-probe.ts` 对 model/tool 名脱敏，usage 仅保留数值白名单，限制响应大小/请求次数/超时 | model-probe 本地 HTTP 恶意反射测试 |

权限仍以 API 的 owner 查询为准，SSE/日志/QR 经平台鉴权；共享管理接口仅 loopback + 随机 token，不面向普通用户开放。受控工具只允许当前 Bot 的发送与群历史；工具枚举和越权隔离有 native SDK fixture 验证。

## 保留的真实限制

- 同 Python 进程不是不可信代码沙箱。受控插件仍可因进程级崩溃影响同组；需要任意插件的 Bot 用 dedicated。
- 共享组内按 Bot 统计请求和逻辑额度，不能用单 PID RSS 精确分摊每 Bot 私有内存；OS 内存上限在组层执行，单成员 CPU/内存强隔离未实现。
- 独立 PostgreSQL 分支本轮未实机验收，嵌套 transaction 连接复用问题仍待修复；当前产品运行 PGlite。
- 随机组健康端口有数据库唯一约束，尚无完整 OS 端口抢占重试。
- 历史、媒体与事实保留策略不等同完整 SaaS 配额和备份恢复体系；本轮只限制显式事实及日志缓冲。
- 模型探测有请求预算；全产品细粒度租户 API 限额/费用账本和全局模型并发调度尚不完整。
- 共享组默认容量 4 是资源/故障范围的折中，不代表 8 不可行，也不是四真实付费客户已经验收。

原始失败证据保留：`full-tests-attempt1.log`、`full-tests-final.log`、`real-scenarios.log` 等。没有删除旧测试或掩盖失败；后续修复分别重跑，最终以本轮最终日志为准。
