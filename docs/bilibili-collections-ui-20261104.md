# 合集、小程序分享卡片、能力页面与思考强度交付记录

日期：2026-11-04。范围以用户最后的优先级为准：先完成合集、小程序和 UI，追加模型思考档位发现及小双白改为中档。不把此次交付视为整个历史优化计划全部完成。

## Plan vs Actual

| 要求 | 实际实现与职责 | 证据及状态 |
|---|---|---|
| 内层能查看合集，UI 可控 | `collections` 分别返回 UP 主的合集、系列；`collection_videos` 分页返回真实 BV 目录；`video_info` 返回所属合集引用。`collectionsEnabled` 默认开启，但仍要求 B 站总开关及 `public.read`。撤销后排队任务在执行末端重新核验。 | Windows 隔离账号/数据库测试和 Linux 隔离回归通过；线上工具清单已发布。真实 B 站接口验收受风控阻塞，不能标为账号实测通过。 |
| 小程序分享链接进入公共上下文 | 将 OneBot JSON 卡片中有界的标题、链接转换为不可信文本，保留原始事件、发送者和消息 ID；不把封面当成视频画面。新增 `resolve_video` 解析官方视频地址及 b23.tv 短链。 | Windows/Linux 卡片解析 3 项测试、短链跳转及拒绝任意地址回归通过。部署后尚无新的真实卡片收发验收证据。 |
| B 站、主动模式页面与现有设置统一 | 沿用现有配置侧栏及表单样式；分开概览、账号、权限、模型资源、目标、节奏、疲劳、预算和执行记录。页内菜单共用草稿，保存仍走原 API。 | 桌面及 390px 手机浏览器回归通过；草稿保留、真实鉴权 API 保存、无横向溢出、无 QQ 启动命令通过。保留其他 AI 已完成的 Windows 前端与背景。 |
| UI 获取 API 允许的思考档位 | 模型列表只暴露供应商明确声明的受支持档位/布尔能力；不根据模型名称猜测。缺失声明时保留手动选择，并明确未验证；可选“不传参数，供应商默认”。 | 模型发现、凭证过滤、账号归属及原生 SDK 参数共 5 项回归通过；浏览器验证声明的低/中/高档位及中档保存。 |
| 小双白改为中档 | 仅修改 `reasoningEffort=medium`，原模型、URL、Key、QQ 登录与其他配置保留。 | 配置 v38 已应用，QQ 代次不变；Linux 原生 `custom_extra_body.reasoning_effort` 为 `medium`。实际模型为 `gemini-3.8-flash`；供应商 `/models` 返回该 ID，但未声明档位范围，不能声称已发现它的完整能力边界。 |
| 安全部署 | 精确文件清单、修改前基线与目标摘要检查、双目录备份、原子替换，HTML 在资源后发布；保留旧哈希资源。 | Linux 统一源码与运行目录产物摘要核对通过。API 独立更新；B 站执行器在原 Agent 内替换；共享 AI 空闲后加载 Python 适配代码。Agent PID、4 个 QQ 协议 PID/创建时间及 QQ 代次不变；共享 AI 4 成员 ready，无成员错误。Guard 保持 active。未执行 Git 写操作。 |

## 根因及边界

14:40 的小程序事件进入原生转换器后，JSON 组件没有进入 `message_str`，群上下文没有获得可用标题和链接。其后的“师徒四人在干什么”缺少视频身份。此次在已有入站媒体规范化阶段补齐卡片文本，不另建消息入口、身份、记忆或投递路径。

短链仅接受 HTTPS 官方视频地址或 b23.tv；每跳重新检查主机、路径和凭证，最多四次跳转，不携带 B 站 Cookie，不跟随任意外部/本地地址。遇到风控不绕过。合集仍是目录元数据，内层需再按 BV 调用字幕或片段分析，不能把查目录称为看完视频。

14:45 显式 BV 消息的首条回复约 56.7 秒：前置合并约 5 秒，模型阶段约 49 秒，投递等约 2.6 秒；后续记录有详情、字幕尝试和标签查询，没有画面分析证据。用户已将延迟优化移至后续，本轮不改群消息合并窗口或模型超时策略。

## 实际验证

- Windows：B 站后端 32/32；模型发现与原生思考参数 5/5；分享卡片 3/3；最近图片关联、跨发送者/会话隔离、过期与迟到取消脚本通过；B 站工具取消/配置换代脚本通过。
- Windows：`tsc --noEmit`、`npm run build:production` 通过。Vite 的单 JS 块超过 500KB 提示仍存在，未通过放宽阈值掩盖。
- Windows：Playwright 桌面/手机页面、B 站开关与合集保存、主动配置草稿与保存、API 声明的档位选择和中档保存通过，无浏览器页面错误。
- Linux：隔离目录内运行新合集完整调用链、短链解析、思考档位元数据 3/3；分享卡片解析 3/3。隔离数据库未使用生产账号。
- Linux：4 个 QQ 在线，Agent 和协议原进程保留；共享 AI 更新后 ready、4 个成员无错误。新工具 `collections`、`collection_videos`、`resolve_video` 出现在小双白的真实桥接清单。当前线上 JS/CSS 资源 HTTP 200。
- Linux 公开只读探测：第一步视频详情被平台返回风控，立即停止，未继续合集请求、未使用账号凭证、未执行写操作。不能用模拟回归替代真实平台验收。

第一次浏览器补充测试定位模型输入框时用了错误的 `textbox` 角色（带 datalist 的输入实际是 `combobox`），改为准确的字段标签后完整通过。第一次线上工具清单核验读取了错误字段 `tools`，实际合同字段为 `operations`；修正核验脚本后通过。这两项均未通过削弱产品测试处理。

## 文件及接入关系

- 公共合同及末端权限：`packages/contracts/bilibili.ts`、`packages/database/tool-effects.ts`。
- Node Agent 工具：`apps/node-agent/src/bilibili/executor.ts`、`web-adapter.ts`。
- 原有 AstrBot 入口：`adapters/astrbot/incoming_media.py`、`bilibili_tools.py`。
- UI：`apps/web/src/capability-layout.tsx`、`bilibili-controls.tsx`、`proactive-controls.tsx`、`model-fields.tsx`，以及 `style.css` 内局部 capability-page 样式。
- 供应商能力清单：`packages/shared/model-catalog.ts`、浏览器安全的 `model-catalog-options.ts`。
- 测试：`tests/bilibili.test.ts`、`model-catalog.test.ts`、`bilibili-browser.ts`、`shared_card_test.py`。

仍复用 Work、真实租户身份、ToolLoop、工具授权、执行回执、上下文及原发送管线；没有增加常驻服务、第二套调度或视频处理进程。没有改变既有 memory.high、PAGEOUT、zswap/Swap 和 Guard 参数，也未新增真实 B 站写入授权。

## 回退与未完成项

Linux 发布目录 `/opt/fishbot-pg-cutover-20261031/control-final-c/bilibili-collections-ui-20261104` 保留 manifest、安装记录、精确旧文件备份和摘要校验的 `publish.py rollback`。回退前拒绝覆盖发布后又变化的目标。回退代码后需独立重载 API/B 站执行器，Python 代码需在共享 AI 空闲后加载；不重启 QQ。小双白如需回退档位，应通过配置 API 将该字段改回先前的 `high`，不能恢复整份旧配置覆盖后续修改。

待实际条件满足的验收：风控解除后验证真实合集/系列目录；一条新的真实 QQ 小程序卡片及后续提问，核对视频身份解析、授权分析工具和消息回执。当前没有新的视频理解质量或聊天 P95 压测证据，不宣称这些指标改善。
