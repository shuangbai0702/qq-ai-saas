# 实例与真实协议操作

## AstrBot

固定参考版本见 `references/astrbot-version.json`（本轮 `dad255c60f7983fd63c7c0282ded1dde859fbc9a`，4.29.3）。`scripts/setup_astrbot.py` 复制上一轮只读快照到本项目；不存在时 Clone，并记录 SHA；`uv sync --no-dev --frozen`。共用代码/venv，每机器人单独 `ai/data`、SQLite、插件数据、媒体、tmp、CWD、`ASTRBOT_ROOT`。不运行 dashboard。

`AstrBotAdapter` 生成 upstream `data/cmd_config.json`；`headless.py` 调用原版 lifecycle，写入真实 Persona DB 并重新加载 persona manager。模型 Key 仅在该 AI 进程 `FISHBOT_MODEL_KEY` 中，配置使用 `$FISHBOT_MODEL_KEY` 引用。AI 端口是 OneBot reverse WS；+1 为仅 loopback、token 验证的代次/版本健康回执。所有机器人端口只绑定本机。

配置保存增加 revision；运行中通过受控重启应用。applied_revision 必须等待该代次、版本的健康回执，配置写到磁盘不算已生效。模型已配置与供应商调用成功分别显示。群白名单在 aiocqhttp 消息入口拦截，私聊不受群白名单误伤；触发与会话由真实 AstrBot 管理。

## Euler / hiro

安装 `npm run setup:euler`。固定 Euler SHA `027521d6b53a54c3156292ebabc56a4bc314ba03`；lock 中 hiro `6ec1d883f1e54e13d8a211df20e05d2a782a77ea`。独立进程 cwd=`<bot>/qq`，会话落在该目录。页面需填计划 QQ 号。

```dotenv
EULER_SIGNER_URL=http://127.0.0.1:你的签名端口
EULER_SIGNER_TOKEN=实际Token
```

上游 `protocol/__init__.py::_build_lagrange` 将此 URL 拼接 `/api/sign/sec-sign`，Token 作为 basic-auth 用户段。需要**兼容相同协议版本**的签名实现；删账号配额不会生成签名。本轮没有自建兼容签名服务，没有宣称已解决外部额度。

`bridge.py` 在进程内包装 hiro `BaseClient.fetch_qrcode/get_qrcode_result`、`Client.register`、Euler `set_online`。PNG 不依赖终端字符二维码；48等待扫码、53等待确认、0认证、17过期、54取消；只有 register/online 事件有真实身份才认为在线。掉线后撤销在线状态。禁止阻塞 `input`，需要额外验证时报告 `verification_required`，**挑战提交尚未实现**。不修改参考源码磁盘文件。

独立真实协议入口（不启动 AI，最多等待120秒，退出停止进程）：

```powershell
$env:LAB_QQ_UIN='你准备扫码的QQ号'
npm run protocol:lab -- euler
```

二维码写 `.local/protocol-lab/qr.png`，状态输出控制台；签名错误/QQ网络错误应查看该目录 `bots/lab/runtime.log`。本轮仅真实导入及 schema/hook 检查通过，未配置签名、未生成真实 QQ QR、未扫码或收发消息。

## NapCat

第二轮已提供实际安装器和受控 Runner：`npm run setup:napcat`，官方 v4.18.41 Windows.Node 包，版本/SHA校验与独立QQ profile/config/cache。平台自动生成每 bot 管理凭据与 loopback 端口，不再需要手写包装器。私有接口只由后端访问，真实 QR 已通过平台鉴权接口返回。详情与命令见 [第二轮 QQ 记录](round2-qq.md)。

尚未扫码验证登录后收发和会话恢复；可执行入口不等同这些验收通过。Euler 当前真实尝试在生成QR阶段即依赖有效signer，具体失败日志保留。

## 生命周期与边界

DB 持久 desired/observed state、generation、revision、commands。幂等键去重，旧代次命令 superseded；旧扫码取消不停止刷新后的实例；QR/SSE 按 Bot 所属租户授权。唯一 qq_bindings 防重复绑定；恢复的会话同样重新校验身份。PID 需 argv 包含独立 bot 路径才允许停止。

每次 Agent 运行最多连续恢复3次，指数退避；健康60秒后重置计数。显式操作重置恢复预算。预算在内存，重启 Agent 会重置；尚非跨重启的持久熔断器。用户停止则不自动拉起。删除停止进程、删除 Key/QQ绑定并软删 bot；数据目录保留，尚无永久清理功能。

## Linux 目标

第二轮通过便携 QEMU 实测 Linux 控制面和PSS，完整 Linux QQ+AI 与 cgroup 尚未验证。安装 Node、uv、PostgreSQL，`npm ci; npm run setup:astrbot; npm run build:production`，`.env` 设置 DATABASE_URL、APP_ORIGIN、COOKIE_SECURE、Linux python 路径。目标以 `scripts/fishbot.service` 运行统一入口；真实部署前调整路径、TLS反向代理和OS隔离，模板未安装到本机系统。

Linux 可选 `FISHBOT_CGROUP_ROOT=/sys/fs/cgroup/<已委派且启用cpu/memory/pids的父组>`。Supervisor 为每bot建立子组，共同约束 AI+协议进程：默认 memory.max=1GiB、pids.max=128、cpu.max=100000/100000。配置了该根但无法应用配额会拒绝启动；本轮只做代码/构建检查，未验证控制器委派和执行效果。Windows 没有 Job Object 配额实现，只采集 RSS，不宣称有硬资源限制。

API 与 Agent 现阶段默认同进程（控制职责分离、不是物理拆进程），避免 PGlite 多进程写。外部 PostgreSQL 可 `EXTERNAL_AGENT=true npm run api` 配合 `npm run agent`；此组合本轮未实机跑通，不应误认为已经多节点调度。
