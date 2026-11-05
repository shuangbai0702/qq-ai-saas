# FishBot Linux 内存策略实地复核 · 2026-11-06

本次为只读生产检查；未修改保护参数、产品代码或重启任何服务。主机 /usr/local/sbin 的 controller、guard、pageout 与仓库 scripts 对应文件逐行一致（忽略换行格式）。单位 MiB；内核换页计数不等于磁盘设备实际 IO。

## 结论与证据

现有策略能回收，但出现明显施压/回退震荡，不能称为已稳定优化。约03:53–03:54，30.0007秒被动采样：初始disabled不存在、some/full avg10均0；结束disabled为host-memory-full-stall-over-5s-in-20s，some avg10=63.37、full avg10=56.95。full累计增加16.774秒。pswpin增加25568页（约99.88MiB），pswpout增加104813页（约409.43MiB），major faults增加30758（约1025/秒）。可用内存451.7→738.8MiB，Swap逻辑1097.6→1376.9MiB，zswap池46.5→26.8MiB。RAM下降伴随显著stall，不能只看内存低点算收益。最近2小时Guard同一压力原因回退21次。此次未运行模型探针或回归负载于Linux，采样只读。

## 已证实现问题及边界

1. **类别总限额与运行实例不对应**：controller把5个NapCat归入同一个fishbot-napcat，把2个共享运行时归入同一个fishbot-shared-ai，空闲memory.high分别仅48MiB。不是每QQ/每共享进程各48MiB；任一成员活动又会放宽整类。高密度下容易过度压缩空闲成员，或被另一成员连带唤醒。48MiB是soft throttle，不是PSS上限，也不是启动时分配的物理预算。
2. **PAGEOUT扫描预算不够细**：主机linux-pageout仅有循环间3秒检查，没有每轮字节额度；整段映射一次process_madvise，在调用内不能根据新的活动立即取消。日志示例每次advisedVirtualBytes约159–851MB；这只是被建议的虚拟映射长度，不是实际释放量。controller每进程40秒下限与退避存在，但不等于只扫描真正长期冷页。
3. **恢复直接重施压**：Guard回退放开全部注册memory.high；冷却后controller重新初始化previous/last_busy/reclaimed/futile，18秒后可直接恢复48MiB，而非渐进恢复，也丢失进程级上次PAGEOUT及无效退避。内存有短期下降超过1MiB即当有效并重置到40秒，未检查后续是否很快换入。现场观察到恢复→stall→再次回退，但各组件占多少停顿仍未隔离归因。
4. **最大冷却与计数窗口相冲突**：guard.rollback只在上次回退距今<600秒时累计失败；最大cooldown恰为600秒，且恢复还要求连续无reason，实际再次施压必更晚。即使刚过最大冷却立刻再失败，也会退回count=1/cooldown=30秒，不能证明此前已稳定。现有测试明确允许>600秒清零，因此测试通过并不代表这项策略合理。
5. **公共组件只靠CPU推断空闲**：activity_wakes只服务shared/dedicated AI，API、NapCat、PG等依每秒CPU差>0.2秒判断活动。网络/数据库等待中CPU低不代表任务结束；可能对仍参与实际请求的公共组件施压。主机PAGEOUT还会扫描Agent（日志约800MB映射）。这是识别盲区，未证明某条QQ延迟由此造成。

## 已确认有效及未发现的问题

- Guard真正工作，并且failedPaths为空；PID birth、NSpid与cgroup验证存在，PAGEOUT使用pidfd；未看到OOM kill证据。
- AI前后台真实工作有activity，RuntimeCare每60秒为busy任务刷新，控制器接受120秒内活动；并非只有开始/结束两次写入。
- Agent运行环境FISHBOT_MEMORY_POLICY=off，内置runtime_memory.py虽仍有代码入口，线上不做第二套实际PAGEOUT。
- 短暂max可来自真实活动或Guard回退，不应视为48MiB配置没部署。controller正常退出由systemd每10秒重启不等于崩溃。
- zswap已启用shrinker、lzo/zbud、max_pool_percent=25；当前池远低于容量上限，不能靠继续砍池上限保证净收益。
- containerd主进程前轮实测PSS约195MiB，宿主其他服务也占RAM；不是全部由QQ导致。不可为降低它而贸然重启Docker。

## 建议修复顺序（本次未实施）

P0：保留Guard阈值，修复退避计数对“冷却”和“稳定运行”的混淆；重启保留按PID+birth记录的回收时间和退避；压力后分阶段恢复施压，避免同时压全部组件。

P1：主机PAGEOUT加单次字节/分块预算、活动与身份复核；基于回收后换入速度/PSI判断净收益并退避。保持40秒为最短间隔，不把它作为固定强制周期。

P1：区分类别统计与运行实例治理。NapCat按实际QQ进程树、共享AI按真实运行组治理；不要按配置成员重复统计共享AI。是否保留类别48MiB还是设为每运行实例48MiB须明确，因为两者资源行为不同，不在本轮悄悄改义。

P2：API/PG等接入实际参与请求的有限活动lease，不恢复“AI活动一律唤醒全部组件”；CPU只是辅助。避免增加每QQ常驻进程或高频全机smaps扫描。

验收：同负载基线/候选，记录RAM、PSI累计差值、pswpin/out、磁盘设备IO、major faults、实际QQ回执和P95；低点PSS不作结论。保留48MiB、40秒、Swap/zswap、离线清退及回滚能力，逐项受控变化，不同时盲调多个内核参数。

## 验证范围

Windows纯逻辑回归：linux_memory_controller_test.py 10项、linux_memory_guard_test.py 6项通过。Linux真实文件、systemd环境、cgroup、回退日志及30秒内核采样已读。没有新版本部署、长期消息P95、磁盘IO隔离归因或参数A/B；不宣称这些已通过。原始私密日志留.local，不进入报告。


## 实施追加：用户要求每实例48MiB，保持强回收（04:19–04:29）

前面的“未实施”为只读调查阶段事实；随后用户授权优化，并明确48MiB应为每实例。已修改并部署scripts/linux-memory-controller.py、linux-memory-guard.py、linux-pageout.py；新增tests/linux_pageout_test.py，扩展controller/guard测试。未改Web、Agent业务、AstrBot、NapCat、QQ登录数据；未执行Git写操作。

### 实际实现

- 每个NapCat进程树独立runtime-PID-birth叶组，空闲memory.high=50331648；每个实际共享AstrBot运行进程同值；独立AstrBot16777216。共享成员不增加共享运行时配额。主机按进程树识别子进程、PID出生值防复用，空叶清理。当前5个NapCat叶组、2个共享AI叶组。
- 保留18秒空闲宽限、AI忙时max、40秒PAGEOUT最短间隔、memory.max不设硬限、Guard原压力阈值、zswap/Swap与离线清退。公共API/PG原128MiB最终目标保留。
- 恢复限额先从当前记账量开始，每组每5秒最多降低32MiB，各组施压错开至少2秒，最终仍回到原48/16MiB。不是持续放开阈值换取低压力。
- PAGEOUT单次虚拟扫描64MiB、单块4MiB，每块复核PID出生、cgroup、Guard与活动标记。继续扫描游标跨轮推进，避免反复只扫堆头。单个syscall仍无法立即取消，但每块有界，退出关闭pidfd；helper退出/取消会回收，进程复用不提交结果。
- 将nextAllowed、无效次数、游标按container ID/boot ID/PID-birth存盘，控制器重启保留退避。无效回收从80秒逐步退至最高600秒；有效回收仍允许40秒。未增加周期性模型或每QQ常驻进程。
- Guard连续失败计数不再把600秒冷却当稳定运行；只有实际armed且持续无失败600秒才重置，Guard重启不认领未观察期间。冷却原30–600秒和PSI阈值保留。
- 多实例活动文件每轮只读取一次；活动只唤醒真实AI运行实例，不统一唤醒API/PG或其他共享组。

### Linux旧内存记账的过渡边界

cgroup v2移动PID不会同步搬迁之前已分配页的charge。真实观察：新叶组memory.current很小，但PSS仍存在，不能把叶组charge当PSS。为不重启QQ，父级保留按实际实例数计算的总包络：当前NapCat=5×48=240MiB、共享AI=2×48=96MiB；叶组各自48MiB。包络不是额外加出的RAM，也不是把所有QQ共限48MiB。父级在任一成员实际忙时可放宽，空闲叶仍保持其限额。

该机制覆盖旧记账的总量，但**不能把旧页逐一强行重新记账到对应叶组**，因此当前在线进程历史页的逐实例charge隔离不是无缝追溯迁移；在现有process_madvise与正常释放/新分配中逐步回收。没有因此重启QQ或虚称每个进程PSS必低于48MiB。此为维持在线与内核记账的明确限制。

### 验证与运行数据

Windows与Linux均25项：controller15、guard7、pageout3全部通过。覆盖进程树各自限额/共享组不按成员分配、活动实例隔离、恢复渐进步长、重启退避和boot/container失效、冷却不算稳定、64MiB预算与游标、4MiB分块之间唤醒/迁组/关闭fd。最终source/runtime/host12个目标文件哈希校验一致。只重启主机controller/guard以加载脚本，Agent PID730881、5个QQ协议及2个AI PID/创建时间均与本次上线前一致，5QQ仍在线，控制器和Guard active/running，disabled不存在。

不同负载的短观察，不能当相同输入的严格A/B：

|窗口|RAM平均MiB|Swap起止MiB|zswap池起止MiB|累计full停顿|Guard回退|回收状态|
|---|---:|---|---|---:|---:|---|
|旧异常30秒|未采均值|1097.6→1376.9|46.5→26.8|16.77秒|1|恢复后再回退|
|上线前120秒|1312.4|1077.7→1023.4|19.2→17.1|0.264秒|0|全程Guard冷却，非有效强回收基线|
|最终包络150秒|1196.3|1238.5→1250.7|76.5→72.8|1.821秒|0|全程启用；末段仅优化活动文件重复读取|
|最终文件90秒|1199.6|1305.2→1224.8|91.9→66.1|1.198秒|0|全程启用|

最终90秒pswpin增23436页、pswpout增13591页、major faults增113533；vda读206008扇区、写147552扇区，设备IO时间增4811ms（512字节扇区计约100.6/72.0MiB设备读写，含数据库/其他业务，不是纯Swap IO）。仍有换页和缺页，不能只报RAM降低。20秒进程定位：Agent major faults26699/CPU7.53%，活跃AI4410/12.82%，5NapCat各2719–3170 major faults但CPU0.3%–0.45%，控制器CPU2.1%。这些为一核百分比短样本，非长期均值。仍需针对Agent工作集/历史charge做后续受控研究，不以扩大所有阈值掩盖。

一份瞬时PSS采样：5NapCat合计90.8MiB（USS65.0）、2共享AI45.2MiB（USS34.8）、Agent100.5MiB。它们是实际进程PSS，与cgroup charge不同，也不与Swap相加算物理RAM。

### 回退与Plan vs Actual

主机发布目录：/opt/fishbot-pg-cutover-20261031/control-final-c/memory-strategy-20261106/release，含before/、preflight.json、manifest.json与rollback.sh。回退脚本先停controller/放宽限额、移回原类别布局，再按文件哈希恢复原脚本、恢复Guard/controller；未自动执行回退测试，因为会撤销线上每实例配置。原文件保留，数据不回滚。采样脚本均短时退出，没有常驻测试进程。

计划对应：实例配额（用户新增明确要求）、分块预算、持久退避、Guard冷却修复和渐进恢复已实现/部署；原保护、40秒、真实共享实例语义保留。合理扩展为父级N实例包络，以处理实测Linux旧页charge不随PID迁移；并增加一次读取活动文件防止实例数量带来重复扫描。

未完成项明确保留：未接入公共API/PG真实请求lease（需独立业务接线，原CPU活动规则保留）；未实现按未来换入率的完整净收益控制（当前仍以回收前后RSSAnon和失败结果退避）；未验证严格同负载A/B、真实消息P95≤额外5秒或长期多租户高峰。在线状态/原进程不变不等于真实消息性能验收，不能声称这些已全部完成。
