# Linux 容量优先镜像记录（2026-10-30）

目标：2 GiB RAM 尽可能承载 10 个 QQ，接受额外约 5 秒冷恢复延迟。用户要求停止新增内存实验，直接取合理值并制作镜像；此目标不等于本次已取得十号容量或延迟验收。

## 采用值与依据

- 空闲资格 60 秒；每活动周期一次；尝试冷却 300 秒；每轮最多一个树操作。
- Linux 仅在 MemAvailable <= max(512 MiB, MemTotal × 35%) 时尝试回收；2 GiB 对应约 717 MiB。35%/512 MiB 来自昨天宿主控制器的压力门槛，替代讨论中的 30%。
- Linux MemAvailable < 384 MiB 时不执行主动原生操作；Windows 保留 192 MiB。该线是主动操作保护阈值，不是物理内存预留或 OOM 保证。
- 仅建议私有、匿名、驻留、未锁定页换出，每棵树每次 64 MiB 地址预算。建议地址长度不等于实际释放量。
- 保留所有权/创建时间检查、pidfd、活动文件保护、CPU活动判断、系统降级。当前 CPU 采样仍以根进程为主；共享 AI 的活动文件用于补充实际请求状态。

昨天 `/opt/fishbot-memory-lab-20261029/evidence/adaptive-controller.jsonl` 的 B 样本 30：private_ws=13,688,832 B（13.05 MiB），RSS=199,266,304 B（190.04 MiB），PSS=105,766,912 B（100.87 MiB），swap=85,446,656 B（81.49 MiB）。空闲期间低私有工作集确实存在，不是整树 RSS 降到十几 MiB。对应实验有双真实 QQ、额外 512 MiB 受限负载、2 GiB 专用 swap、zswap zbud、swappiness=200、空闲 memory.high=32 MiB，idle/cooldown 加速为15/30秒。文本投递约181–225ms，图片约495–808ms；不含平台/AI全栈，也不是十号长期验收。

## 部署边界

当前镜像为多个 QQ 加共享 AI 的单应用容器，没有每 QQ 独立 cgroup。因此讨论过的每 QQ memory.high=96 MiB、活动256 MiB及memory.max=256 MiB没有作为镜像行为实现，不能套到整个应用容器。镜像保持非root，不开启 privileged、不挂载宿主cgroup写权限，不自动改宿主swap/zswap/sysctl。

成功主动换出需要宿主swap与内核/权限支持；先前默认受限容器process_madvise返回EPERM。无swap或拒绝时降级系统管理，镜像本身不能保证复现宿主实验的内存降幅。5秒为可接受预算，未验证为上界。

## 构建与验证

本机 `npm run build:production` 完成（含tsc）。按用户指示不再运行内存实验/新增QQ消息。测试预期随阈值同步，但修改后未执行；历史21项结果不能视为新阈值回归。

使用新allowlist上下文 `.local/docker-context-capacity-20261030`，1794文件，AstrBot commit dad255c60f7983fd63c7c0282ded1dde859fbc9a。镜像计划采用已固定security-r3基础镜像进行应用层刷新，依赖锁文件及upstream逐项与旧上下文比对后方可复用。最终镜像ID、导出校验和、健康结果待构建完成补录。

## Plan vs Actual

按用户最新指示停止真实内存/延迟复测，改为基于旧数据选择参数并交付镜像。已实现压力阈值和384MiB操作保护；未实现每QQ cgroup限额和实时放宽机制，因为现有单容器架构不具备此边界。保留旧镜像、会话和数据。十号容量、冷恢复5秒、容器主动pageout权限仍未验收。
