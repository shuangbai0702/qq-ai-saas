# B目标上下文测量与有限认知回归

在仓库根目录运行，使用项目真实SDK的Python环境。A/B须在两个独立进程运行，避免同进程重复安装全局SDK hook的夹具干扰：

```powershell
references/astrbot/.venv/Scripts/python.exe -B experiments/b-goal-context-20261113/measure.py A
references/astrbot/.venv/Scripts/python.exe -B experiments/b-goal-context-20261113/measure.py B
```

可在模式后传输出目录，默认`.local/b-goal-context-measurement`。使用本地HTTP响应替身，真实Provider客户端、SQLite Journal和主动激活实现，不发送QQ/B站消息，不产生模型费用。35次激活含两目标、修订变化、一次真实SDK工具续接。每次检查物理历史前缀不变，B独立重放与权威夹具相等；35后实际退休/重建由`tests/activity_goal_delta_checkpoint_test.py`另行验证。

所附A/B JSON为2026-11-13实际测量值，单位是Unicode序列化字符，不是计费Token。时间戳长度可产生少量字符差异。本轮最终B读取提醒已纳入B测量。样本规模、目标长度和工具频率会改变收益，不代表任意生产负载。

`model-cognition-sanitized.json`保留实际生产Provider两次有界认知调用的最终回答与usage，未保留推理正文、Key或真实聊天。两者为同一模型的单组A30/B30对照，输入Token26711/20979；缓存输入2304/0，不能将输入Token差直接视作相同比例账单。它们都识别最新revision9及unknown未完成。此证据产生于最终baseline阅读提醒强化之前，不能冒充最终文本的再次模型验收。

该旧认知夹具的effect参数不符合当前完整analyze Schema（3个时间点且身份为占位文本），因此只证明目标修订和unknown判断，不能证明实际pending工具能被合法派发或长期认知稳定。有效参数下的pending幂等由`bilibili-analysis-responsibility.test.ts`、`bilibili.test.ts`和真实数据库预算/恢复测试验证。有限真实模型认知不等于长时间生产质量保证。
