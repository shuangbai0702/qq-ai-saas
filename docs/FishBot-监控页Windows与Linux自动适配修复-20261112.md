# 监控页Windows/Linux自动适配修复

## 问题与证据

用户反馈移动云监控页Swap、zswap及其他指标未同步，并要求自动识别Windows/Linux。实际生产采集覆盖完整、无错误；问题并非所有数据停止采集。

修复前正式域名鉴权接口显示：`processSnapshot.host.swapUsedBytes=168247296`、`swapTotalBytes=8589934592`，但模型无关的监控视图`overview.swapUsedBytes=null`，连续三次CPU值也为null。进程RSS存在，PSS为null。由此证明采集、API拼装和展示的合同不一致。

## 变更

提交：`ecf950b0e69dfc429a06663387332020f49b2446`。

- `apps/control-api/src/memory-monitor.ts`：采样记录`os.platform()`；Linux优先使用原`/proc/stat`计数，缺少procfs时使用Node `os.cpus().times`累计计数计算相邻采样CPU。无需按机器手动配置。
- `apps/control-api/src/memory-view.ts`：保留Linux `/proc/meminfo`优先级，接通现有Python采集器的Swap total/used数据；保留0与unknown的区别。API视图携带运行平台，不暴露配置、凭据或命令参数。
- `apps/web/src/admin-memory.tsx`：按服务端平台自动展示。Windows主表与组件趋势使用真实RSS/USS，不将RSS填进PSS字段；逐进程Swap、zswap、PSI、cgroup标为不适用，不伪造0。Linux继续使用PSS/Swap、zswap和PSI原合同。系统换页数值标为估算，并说明不能与RAM相加。Windows zswap不适用不代表Windows内存压缩为0。

Windows换页统计沿用当前安装的psutil口径，不宣称是磁盘I/O或精确逐进程磁盘驻留；平台口径不能机械等价。[psutil官方API说明](https://psutil.readthedocs.io/stable/)用于核对指标含义。

没有改动Bot/租户状态、模型、认知、Journal、任务、权限或工具账本，没有创建第二套采集状态系统。其他会话对Python与测试文件的已有及后续改动均未混入。

## 验证及限制

- 17项监控针对性Node测试通过：Windows数据接线、0与未知、Linux优先级、进程归属/去重、过期和采集错误、CPU计数及历史生命周期。
- 10项`memory_snapshot_test.py`通过；类型检查通过。
- 从独立干净Git worktree构建生产包成功，build.json记录本次提交且gitDirty=false。有既有JSON导入属性、注释和chunk大小警告，未屏蔽。
- 实际构建Web的聚焦浏览器验收通过：Linux原展示、Windows切换、零换页、不适用、RSS列、共享成员、移动窄屏及无运行时JS错误。浏览器使用Linux历史诊断fixture及合成Windows字段，不冒充Windows真实接口UI截图。
- 较宽的原浏览器脚本遇到更新公告弹层，补充正常“知道了”操作后，另在过期快照阶段超时；未把整套浏览器测试写成通过。聚焦验收没有修改或删除仓库原测试断言。
- 额外运行`memory-runtime.test.ts`在“内部事实保留private来源”断言失败；当前工作区和独立干净worktree均复现。同类认知逻辑和该断言未由本次修改，亦未对它放宽断言。本轮未定位其根因，不能宣称全项目零回归；监控上线范围仅为下面的受限覆盖文件。
- Linux指标优先级通过fixture验证；没有在旧Linux服务器部署本修复或进行其真实持续采样验收。

## 受限部署与生产结果

实际应用基线仍为`8b582b61ec9a69f044bceb298a1f59448757f9ff`，叠加本次监控覆盖层。最终发布11个必要文件，压缩包254,511字节：两个API TS及对应编译JS/maps、页面源文件、构建清单、Web index和活跃JS/CSS。最初打包误包含未改动静态资料，传输已中止，最终改为小覆盖包；没有把整份静态资料部署。

覆盖前比对旧/新构建清单：除两个获准API模块外，所有TS入口、Python、合同、迁移哈希保持一致；同时逐文件检查现场源码与旧构建指纹一致，避免覆盖他人部署。保留原文件和服务身份回退材料，验证tar路径和全部文件哈希后替换。

只重载控制API：PID14252→15044，健康接口恢复；Agent和PostgreSQL记录及进程生命周期未变。没有重启QQ/Worker或重新登录。正式域名HTML引用新JS，公开JS内容哈希与本次构建一致。

上线后正式域名鉴权监控API读回：平台win32，采集完整、无错误；Swap used=221,372,416字节（约211.1MiB）、total=8GiB，与同份Python采集包一致。连续CPU采样为约8.96%、9.06%、7.93%，不再为null。随后回环鉴权接口复核约10.28%，采集持续更新。数值随时间变化，不能把前后占用差异解释成修复造成的内存增减。

生产现场小双白和梁沅芷均online、AI ready、无last_error；大肥鱼保留离线、登录已过期状态，等待本人登录。未为监控验收外发QQ消息。所有临时验证用户和会话均已清理。

## Plan vs Actual

| 要求 | 实际实现与证据 | 状态 |
|---|---|---|
| 监控内容能持续读取 | Swap API接通；CPU连续差值；正式域名真实数值 | 完成所定位缺陷 |
| 自动识别Win/Linux | 服务端OS标记、跨平台采样回退、浏览器按平台展示 | 完成，无人工开关 |
| 不支持不能误报为0或同步失败 | Windows不适用与采集unknown区分，Linux指标保留 | 完成 |
| 不影响已登录Bot和其他工作 | 单独API重载、Agent/PG身份不变、两号仍在线、干净worktree小覆盖 | 已核验 |
| 验证不制造通过 | 针对性回归与聚焦浏览器通过；较宽套件失败如实列明 | 非全项目零回归声明 |

回退仅需还原受限覆盖层及Web index并再次单独重载API；不需要数据库迁移、清空Journal或重启QQ。未对Windows添加Linux指标仿真，亦未额外实现Windows内存压缩/压力的另一套指标。

## 补充：Windows原生指标替代（2026-11-12 22:32核验）

用户进一步明确要求Windows展示对应的原生指标。因此本次扩展前述“不适用”设计；此前结果保留作为历史记录。

功能提交`aa6af36`。为排除另一会话的`209f171`压缩相关变更，基于实际监控基线`ecf950b`在独立干净worktree中只应用本次提交，生产构建版本为`bfd85e09afb1fcf0ee864957e002f0fe2f694943`，gitDirty=false。当前生产仍为原应用基线叠加受限监控覆盖层，不能将其解释为整个主分支均已部署。

### 实现及指标口径

- `scripts/memory_snapshot.py`：Windows读取进程私有提交量；专门读取System子进程Memory Compression的真实工作集，并检查父进程、进程生命周期与读取权限。缺失、过期或权限不足保持unknown，不能伪造0。Linux不使用VMS冒充私有提交量。
- `scripts/windows-host-monitor.py`：沿用现有SYSTEM采集器上报压缩工作集，不把系统压缩进程重复计入FishBot托管进程。
- `apps/control-api/src/memory-view.ts`：接通私有提交量及新鲜主机压缩样本，缺失项不冒充完整合计。
- `apps/web/src/admin-memory.tsx`：Windows账号/进程第三列显示“私有提交”，zswap位置显示“Windows压缩内存工作集”，PSI位置显示物理内存使用率；高级信息及趋势同步按平台展示。Linux指标保持原合同。

私有提交量由RAM或页面文件支持，**不等于实际换出磁盘的字节数**，不能与RSS/USS相加。压缩进程工作集包含开销，**不是解压后逻辑容量或精确压缩页池大小**，已包含在系统RAM用量中。物理内存使用率不冒充PSI停顿压力。

### 验证与上线结果

- Python：`memory_snapshot_test.py`13项、`windows_host_monitor_test.py`5项通过，覆盖Windows私有提交回退、Linux缺省、压缩进程来源、PID复用和unknown。
- Node：memory-view、memory-monitor、memory-host共18项通过；类型检查、独立干净生产构建通过。
- 实际构建的聚焦浏览器检查通过：Linux原展示、Windows RSS/USS/私有提交、压缩工作集、零换页、桌面/移动端及无JS错误。此项使用合成Windows字段，不冒充真实生产截图；前文较宽测试的未通过项未据此宣称已解决。
- 最终覆盖包259,475字节、13个文件。校验旧指纹、允许变更范围、压缩包路径和文件哈希后替换，原文件及进程身份记录已保留供回退。另同步专用采集器副本。
- 只重载API（PID15044→20712）与专用Windows主机监控任务；Agent、PostgreSQL进程身份不变，未重启QQ/Worker。公开域名HTML及JS哈希与本次构建一致。
- 正式域名鉴权接口采集完整、无错误：Windows压缩工作集81,727,488字节（约77.9MiB）；用户提到账号的RSS=30,826,496、USS=24,027,136、私有提交=177,385,472字节（约169.2MiB）。数值是同次采样读回，不代表固定占用或修复减少内存。
- 小双白、梁沅芷均online且AI ready，代际和修订号未变；另一待扫码账号仍离线。未发送验收QQ消息，临时验证用户及会话已清理。

### 补充Plan vs Actual

| 最新要求 | 实际结果 | 边界 |
|---|---|---|
| Windows自动换成原生指标 | 服务端平台检测，压缩工作集与私有提交真实接线，正式域名读回 | 无手动服务器开关 |
| 不继续仅展示Linux“不适用” | Windows卡片、账号列、高级信息与趋势对应调整 | 不把不同OS口径强行等价 |
| 保护正在运行的QQ及其他会话 | 隔离构建、小覆盖包、只重载API与采集器 | 未部署209f171及工作区其他改动 |
| 必要验证并报告偏移 | 针对性测试、构建、聚焦浏览器和生产读回完成 | 不声称全项目回归或Linux真实部署完成 |

无数据库迁移，无Journal、模型历史、业务任务及回执变更。新增指标的长期稳定性仍需正常运行观察；SYSTEM采集器不可用时应显示未知而非零。回退需同时还原覆盖层与专用采集器副本，再仅重载API及采集任务。
