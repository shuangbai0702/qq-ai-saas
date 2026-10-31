# 内存监控重构执行合同

以本轮用户要求为依据，保持管理员鉴权、多租户边界和现有 API/Agent 部署架构；原约定不执行 Git 操作（后续用户授权提交见下文），不重启 Agent、NapCat、AstrBot。

1. 调整只读采集：整机 RAM/Swap/zswap/CPU/压力；容器与进程生命周期归属；采集失败为未知。
2. 建立唯一 PID 汇总：六类组件、在线 QQ 协议树、共享组的配置成员/在线成员/进程数量、离线残留。
3. React 重构：概览卡片、分组表格、整机/组件趋势、高级详情；手机适配、数据过期提示。
4. 验证：聚合/失败/共享去重回归、实际 Linux 采集、构建、桌面和手机浏览器；仅部署 API/Web 和只读主机采集器，前后核对运行 PID。

采样计划为 30 秒、内存中保留 2 小时；旧采样只存趋势汇总，逐 PID 仅存最新。部署前保留当前文件和服务回退入口。最终在本文补充 Plan vs Actual、实测证据与限制。

## 实际交付与 Plan vs Actual

- React 页面已部署到 Linux：整机六张卡片、资源分类、每个在线 QQ 的协议树、共享 AI 的配置/在线成员、离线残留、高级 PID 与容器详情、整机和单组件 SVG 趋势。
- 保持管理员权限；普通租户访问 `/v1/admin/memory` 返回 403。API 返回 allowlist 元数据，不包含健康对象、命令行、环境变量、模型配置或 Key。
- `monitor_host.py` 在宿主机由 `fishbot-monitor-host.timer` 每 30 秒执行一次，只检查四个明确命名的生产容器。原子写入数据卷 `monitor-host.json`；API 没有新增 Docker socket 或宿主 `/proc` 挂载。主机首次采集约 74 ms，协议树约 17 ms；无新增常驻监控解释器。
- 进程以容器、PID、创建时间去重。API/Agent 共用 PID 命名空间，读取 `/proc/PID/stat` 的启动时钟再次校验主机异步快照，排除退出或复用 PID。PostgreSQL 单独命名空间，不因相同 namespace PID 错认 QQ。
- 共享关联配置不复制 AI 内存；QQ 表只含协议树，AI 另列。Linux Swap 使用 SwapPss 分摊共享换出页面。zswap 采用 `/proc/meminfo` 的 Zswap（物理）与 Zswapped（逻辑），压缩池已包含于整机 RAM，不再相加。
- 原计划的 30 秒/2 小时已实现。旧样本只保留整机与分类趋势，逐 PID、账号和原始 cgroup 仅保留最新；一次 API 读取约 19 KiB（两点历史），容量固定 240 点。刷新按钮只读缓存。
- 所有 Agent、NapCat、AstrBot、PostgreSQL 和 gate 的启动时间/PID 保持不变；仅 API 重启。首次部署后补充了异步 PID 生命周期校验，因此再次更新了 API，避免 API 重启窗口旧新进程被合并为两个 API。

### 验证

1. 当前工作区相关回归 21 项通过（API/租户/权限、内存采样、聚合、实际共享 SDK）；补充 PID 校验后定向 11 项通过；进程采集 Python 回归使用项目 venv，10 项通过。
2. 独立的干净 HEAD 加本次监控补丁也完成 TypeScript 检查及 21 项回归，防止遗漏已有脏工作区中的依赖。
3. 生产 Linux API 实测完整采集：1 个真实 QQ、1 个共享 AI 进程、3 个配置关联成员、1 个在线成员；2 个停止和 1 个已清退账号，自有进程均为 0。7 个分类的进程数与唯一 PID 总数一致。
4. Playwright 使用实际 Linux API 采样与同版生产 Web 构建，在 Windows Chromium 进行桌面/390px 手机视觉及交互验收：首次可读约 0.35 秒，组内跳转不破坏路由，切换趋势、过期警告、未知值、无页面横向溢出及无运行错误均通过。失败与过期场景为注入测试，不在生产停服务制造故障。
5. 统一源码前追加的生命周期、安全、请求校验、共享边界和迁移回归 17 项通过。旧安全测试仍期待 288 点，已按实际 30 秒/240 点合同更新，并新增采样周期和过期阈值断言。

这次没有重跑全仓测试，也没有执行旧管理员浏览器测试中的配置修改流程；其页面断言已适配新布局。此次视觉验收不能称为浏览器运行在 Linux 上。

### 内存反弹的实际解释

在线进程的 USS 没有被设定为固定低值。实际连续采样中 NapCat PSS 约 156.6 → 185.8 MiB，而 Swap 约 107.2 → 78.2 MiB；驻留增加约 29 MiB，同时换出减少约 29 MiB，说明主要发生换入，不能凭 USS 一项判断泄漏。动态回收控制器的 memory.high 是软阈值，不能当作 USS 上限。此次监控改造没有收紧运行进程的内存限制。

首次 Linux验收快照整机 RAM 已用 737.6 MiB、可用 1037.7 MiB，Swap 571.3 MiB、zswap 实际物理池 93.9 MiB；这些是时点值，会随流量变化。监控采集的 smaps 读取本身不要求把目标进程的匿名页换入。

### 部署与回退

生产操作和原 API/Web 备份在 `/opt/fishbot-monitor-20261031`。`previous-api-web.tar` 保留部署前文件。`COMMITTED` 后自动回退定时器已撤销；`rollback.py` 的自动入口会尊重该标记。人工回退时应审核并恢复该归档的 API/Web 文件后只启动 API，不恢复旧数据卷，不停止 Agent 或 QQ。

宿主监控脚本安装到 `/opt/fishbot-monitor-20261031/monitor_host.py`，systemd oneshot 的 ExecStart 为：

```text
/usr/bin/python3 /opt/fishbot-monitor-20261031/monitor_host.py /var/lib/docker/volumes/fishbot-production-runtime-c/_data/monitor-host.json
```

定时器 `OnBootSec=10s`、`OnUnitActiveSec=30s`、`AccuracySec=1s`，随主机启动启用。采集脚本故障或文件超过 90 秒时，界面明确显示采集不完整/未知；不会用 PostgreSQL 或 Agent 的 0 MiB 伪装成功。

### 提交范围

原请求禁止 Git；用户随后明确要求完成后提交，本轮以该后续授权为准。最初准备在 Linux 的同一远程仓库检出中制作仅含监控的提交，并已验证该独立补丁。用户随后进一步要求 Windows/Linux 的各种改动统一同步，因此最终提交范围扩大为当前相关源码：监控、已有安全、离线回收、PostgreSQL 及其回归一起纳入，同用现有 GitHub 仓库 main 分支。账号数据、密钥、环境文件和原始生产采样不纳入 Git。生产部署仍只更新本轮所需的 Web/API，不因源码同步重启 Agent 或 QQ。

[脱敏验收摘要](memory-monitor-20261031/acceptance-summary.json) · [部署文件摘要](memory-monitor-20261031/deployed-manifest.json)

完整原始生产数据、姓名/QQ 号和截图只保留在本机忽略的 `test-results/memory-monitor-linux`、`test-results/memory-monitor`，不提交 Git。浏览器复测入口为 `npx tsx tests/memory-monitor-browser.ts`，可用 `MEMORY_FIXTURE_DIR` 指定相同格式的三份脱敏本地输入。
