# 第一轮实施依据（开发前固定）

工作区 D:\MYCODE\APP\fishbot\qq-ai-saas。依据用户本轮17节任务和上一轮08/09/10/12报告；不修改研究快照或旧Coopanion。

| 承诺范围 | 位置 | 验收 |
|---|---|---|
| 注册/会话/租户/机器人CRUD | control-api/database/web | 持久数据库、越权、页面真实调用 |
| 配置版本/Key加密 | contracts/database/shared | 冲突、AES-GCM、无明文返回 |
| 单节点真实进程管理 | node-agent | 启停、退出、重复命令、恢复、防重复PID |
| AstrBot独立实例 | adapters/astrbot | 安装启动两实例、独立root/tmp/端口、人格/provider应用 |
| ProtocolAdapter/QR/SSE | adapters/protocol/mock | NapCat真实接口与Euler事件桥；模拟模式显著标注 |
| 中文SaaS页面/管理员 | web | 注册、向导、配置、连接、日志、统计 |
| 四实例和资源 | tests/scripts | 模拟协议与真实AI分别计证，采样不虚构 |

开发环境无Docker/WSL/PostgreSQL服务，采用持久PGlite（PostgreSQL WASM）与外部pg双驱动；生产目标仍为独立PostgreSQL。真实扫码、手机确认和模型Key不可自行获得，相关部分实现后准确记录未验收。
