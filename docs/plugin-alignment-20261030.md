# 主要插件对齐执行记录

依据用户“插件对齐一下”和前轮缺口表，在原项目增加受控能力；不安装拥有全局可变配置的任意Python插件，不改已有QQ会话与Key。快照：`test-results/plugin-alignment/baseline.txt`。

## 执行计划

1. GroupChatPlus：按参考15字/秒、±30%、最大3秒增加发送等待；可关闭的2%轻量错字；25%被戳后回戳，受冷却、群范围与当前运行代次限制。
2. LivingMemory：增加每10轮后台摘要、持久化和检索、查看/遗忘；复用本Bot模型，有限额、超时、关闭/更新失效保护，不额外常驻Embedding服务。明确不声称完整向量/知识图谱等价。
3. QQProfile/Qzone：仅主人明确指令可用的受控工具及开关，默认不自动改资料/发空间。按当前NapCat源码的真实接口实现；不向真实账号执行无关测试。空间读取若当前端无接口则报告限制。
4. Meme Manager/Proactive：保留已实现独立表情管理、默认关闭QQ收藏、授权主动目标与安静时段；核对参考默认值，不重复装冲突插件。
5. 补充配置管理页面、隔离/迟到/失败回归，执行完整测试、生产构建与检查，再部署现有8787并核对QQ状态。

验收覆盖：配置持久化/独立生效、停止/更新后的迟到发送与摘要不得写回、摘要人物与Bot隔离、主人权限、原始控制标记不得泄漏、网页可操作。真实外部动作与替身结果分别记录。

## 固定源码与接口核对

- 大肥鱼配置包：`zexuanw958-svg/dafeiyu-config-pack`，commit `8c01b0361b8a4baff5be3d2bc67ed55108953086`，`settings/pack.json`。
- GroupChatPlus：本地固定commit `aa97e0e35a18733e73db753238dd2a3021446e42`；`utils/typing_simulator.py`核对15字/秒、随机波动30%、最长3秒。疲劳默认关闭，本轮没有偷偷打开。
- LivingMemory：新下载固定commit `a1e9c20a8c5fab93d6143f51be26fb1bbf8143ec`（2.8.2）；`core/event_handler_modules/memory_reflection.py`、`core/memory_scope.py`核对摘要/来源/作用域/取消旧总结的机制。本平台是独立SQLite受控实现，不声称原插件完整等价。
- Proactive Chat：新下载固定commit `f24e65046a6e4b19c867343ce00e1478774eba4f`；`_conf_schema.json`要求明确私聊会话列表，与现有授权主动目标相符。
- QQProfile/Qzone：Git clone、codeload和本机raw下载均返回404，保留访问失败。网页研究工具能读取缓存的官方源码：`https://github.com/Zhalslar/astrbot_plugin_qqprofile/blob/main/main.py`，`https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/api.py`、`session.py`。这些读取没有取得可核对commit，不冒充固定版本下载成功。
- 实际接口以本机NapCat v4.18.41发布包为准：`references/napcat-windows-node/fishbot-upstream.json`记录包SHA256；`napcat/napcat.mjs`中的`SetQQProfile`、`SetLongNick`、`SetQQAvatar`、`SendQzoneMsg`和`SendPoke`核对参数。空间发布明确传`ugc_right`，默认好友可见；自身空间读取使用固定QQ域名和当前账号Cookie，不暴露Cookie、不跟随重定向。

## 实现职责和边界

- `chat_rhythm.py`：打字等待、极少量口语字替换、真实通知回戳。延迟期间检查当前配置所有权；等待与分条间隔取最大值。回戳不调用模型，每会话60秒冷却。
- `memory_reflection.py`：每Bot一条最多并发1个摘要任务，默认10轮、最多12次请求/天、20秒超时，不重试。原文最多1000轮，摘要最多1000条，注入最多3条。摘要与人物事实分开，不把摘要当已核实身份。群内摘要对同一机器人/同一群共享，私聊按人物隔离；不同Bot绝不跨库检索。没有向量模型/图谱。
- 摘要的派生数据在纠正/遗忘事实时一并清理；清理代次阻止在途旧总结恢复被遗忘内容。管理台可查看/遗忘摘要。Bot停止、替换成员或关闭会取消摘要任务。
- `qq_actions.py`：只为真实主人当前明确指令提供对应工具，禁止内部主动任务、其他人物和跨事件复用。绑定当前事件客户端，不接受Bot ID、任意URL或路径。动作先核实在线账号，预占本消息操作记录，最多20次/天；网络超时不重试以免重复发布。头像只取消息/引用中的QQ图片。
- `behavior.py`在`ToolLoopAgentRunner.reset`阶段注册工具，确保模型声明与Agent执行表一致；仅在Provider层塞工具会导致有声明却执行不了，首次运行测试发现并修复了此问题。
- 新机器人和“应用大肥鱼社交推荐”启用打字、口语错字、回戳、摘要和主人按需资料/空间工具；没有设置主人QQ则资料/空间不可执行。既有用户配置不会批量覆盖；本轮对当前授权测试Bot只补新字段，保留模型/Key/主人/主动对象。
- QQ收藏继续默认关闭；不因插件推荐改变这个单独的即时开关。语音/唱歌、向量检索、完整注意力算法、空间配图/评论/点赞、自动改头像昵称不在本轮已交付范围。

## 验证过程中发现并修复

1. 首次真实Agent测试超时：Provider层添加工具声明没有同步Agent执行表。将注册移到`ToolLoopAgentRunner.reset`，移除重复Provider注册。修复后真实AstrBot Agent通过OneBot发出了受控的`set_self_longnick`动作，模型读取了成功回执；普通群友没有得到这些工具。QQ动作与模型服务为测试替身，真实SDK/Agent/WS执行。
2. 浏览器首次定位失败：控件关联说明文字被并入可访问名称。新增明确的`aria-label`，在真实注册/创建/配置API上验证开关修改、刷新持久化、可见范围和主人QQ保存，以及390px布局。未降低测试断言。
3. 原生QQ只读核对：真实`get_login_info`通过；获取空间Cookie被5秒截止取消，尚未到空间HTTP读取阶段。`live-qzone-read.json`保留脱敏失败。不能据受控测试宣称真实QQ空间读写或改资料已验收。

没有新增常驻服务或本地Embedding模型；本轮没有重新测量内存，不提供虚构节省数字。

## Plan vs Actual 对照

| 计划 | 实现/验收状态 |
| --- | --- |
| 聊天节奏、2%错字、25%回戳 | 已接受控运行层，管理台可配置；SDK延迟和旧配置取消、概率/冷却/目标隔离通过。错字为轻量语气字替换，不等同完整拼音引擎 |
| 10轮摘要和检索管理 | 已持久化、有限额、可查看/遗忘，真实AstrBot调用链通过；检索采用关键词，保留不安装常驻Embedding的低资源路线，向量/图谱未实现 |
| 主人资料/空间工具 | 代码与真实Agent/OneBot测试通过；真实QQ身份已核实，空间Cookie获取超时。未对真实QQ执行改资料/发空间验收，不称完整原插件等价 |
| 配置和前端 | 新增聊天节奏控件、摘要控件、QQ扩展与插件页及覆盖范围说明；真实鉴权API、保存刷新和390px浏览器检查通过 |
| 原有功能与收藏默认值 | 没有删除独立模式、既有测试、权限或收藏能力；QQ收藏总开关继续关闭 |
| 部署与回归 | 最终完整测试和实际部署结果见下方补充 |

合理偏移：原空间插件默认公开，本平台默认好友可见且可调整；控制面统一管理与共享运行组要求受控实现，没有机械加载原插件的全局客户端/配置。没有新增架构服务或第三方可执行插件权限。Qzone读取真实失败为未完成验收项，非Mock成功替代。

## 最终运行结果

- `npm test`：退出0，98/98通过，失败0、跳过0，469.28秒。新增2项配置/生命周期组合测试及1项真实AstrBot Agent运行测试（内部覆盖9个Python用例）；保留原有95项。日志：`test-results/plugin-alignment/full-tests.log`。最后独立专项3/3通过（`targeted-final.log`）。
- `npm run build:production`、`npm run check:production`：均退出0（`build.log`、`production-check-final.log`）。编译源码哈希无不匹配；19个本轮文件哈希归档`source-manifest.json`，差异`review.diff`。
- 隔离浏览器：真实注册、创建、配置保存与刷新持久化、桌面与390px检查通过（`browser.json`和截图）；实际8787浏览器复核也通过（`live-browser.json`、`live-rhythm.png`、`live-plugins.png`）。
- 本地生产已更新，API PID92156，入口`http://127.0.0.1:8787`。当前授权测试Bot保存/生效均v16，QQ自动恢复在线、AI就绪。模型/Key/主人/主动目标保留；新增节奏、摘要及主人按需工具已启用。7张QQ收藏仍保留、自动使用仍关闭（`live.json`）。
- 更新采用正常关闭后备份数据库与Master Key；备份位置见`backup-location.txt`。未删除或重建QQ会话。本轮未对真实QQ改昵称/签名/头像/状态或发布说说；这些能力的实际外部结果仍待后续授权聊天验收，空间读取当前有明确Cookie超时证据。

统一启动仍为`npm run start:production`（端口8787已有服务时不要重复启动）。页面入口：聊天与记忆→聊天节奏/摘要；QQ扩展与插件→主人资料、空间、可见范围；能力管理→摘要查看/遗忘。维护脚本只放`.local`，不作为公开凭据交付。
