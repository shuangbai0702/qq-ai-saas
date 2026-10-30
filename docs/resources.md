# 第一轮实际资源与容量边界

> 第二轮更正：下文历史约662MiB固定样本重复计入了 API 子进程中的机器人。原始数据保留，但该数字不能用于固定成本模型。新采样逐PID去重，正式生产、共享矩阵与 Linux 实测见 [第二轮报告](round2-delivery.md)。

采样环境Windows、Python3.12、AstrBot4.29.3；远程模型，不加载本地模型权重。RSS为进程树驻留内存，可能重复计共享页面，不等于Linux PSS/容器memory.current。样本持续时间短，只代表初始化后空闲，没有真实QQ和图片/语音峰值。

最终四实例原始采样见 `test-results/runtime-measurements.json`：

| 组件 | 单实例MiB | 四实例合计MiB |
|---|---:|---:|
| 真实AstrBot | 240.2 / 240.9 / 245.4 / 273.5 | 1000.1 |
| 模拟QQ OneBot Node进程 | 75.9 / 76.1 / 76.4 / 76.1 | 304.6 |
| AI+模拟协议 | 均值326.2 | 1304.6 |

另一时点开发样本 `live-resource-samples.json`：Vite约117.3MiB，API+PGlite+嵌入Agent约661.5MiB。不能把这个独立时点与上表当作同步系统总峰值；PGlite无法从API RSS中单独准确分摊。生产静态Web不会常驻Vite。NapCat、真实Euler、独立PostgreSQL、签名服务均没有本轮实测值。

完整机器人增量尚未知：测到的是**AstrBot+Mock**，不是QQ协议内存；不能用Mock替代NapCat/Euler数字。四实例已经表明“每用户完整机器人只需15MB”的假设不适用于本实现。2GB是否可用、4GB是否能服务四客户、8/16GB能带多少，均未达到生产验收。

Linux压测入口：用AstrBot venv Python运行 `scripts/sample_resources.py --seconds 1800 --output test-results/linux-idle-4.json`。该脚本采RSS/CPU累计时间；生产容量验收还要补PSS/USS、cgroup、宿主可用内存、媒体/存储和持续负载。按1/4/10/20真实机器人跑空闲、连续私聊、群聊、图片/语音、断线重连、并发峰值。

容量公式（参数待真实Linux压测，不填假数据）：

```text
usable = 物理内存 - OS预留 - 峰值安全余量
fixed = API + DB + Agent + 共享签名/代理
N_memory = floor((usable - fixed) / 每机器人峰值增量)
可批准容量 = min(N_memory, CPU/磁盘/连接/QQ稳定性允许容量)
月托管毛利 = 18.8*N - 服务器月成本 - 固定运维成本 - N*每用户其他增量成本
盈亏平衡N = ceil((服务器月成本+固定成本)/(18.8-每用户其他增量成本))
```

没有真实服务器账单或供应商报价，未虚构摊销/毛利。模型Token由用户承担不计托管毛利，但网络、缓存、日志、监控和备份仍需计入。下一轮应依据实际QQ端/压力峰值决定2/4/8/16GB的容量表，而不是按18.8元收入倒推一个内存结论。
