# 第二轮 QQ 运行端实际交付

## 结果边界

本轮已运行真正的 NapCat/QQ native，独立 profile 成功获取腾讯真实登录二维码；不是 Mock、不是解析终端ASCII。**尚未收到人工扫码和真实模型Key，因此没有验证 QQ 登录后消息、图片/语音、自动恢复。** 不能将未登录内存作为完整在线机器人成本。

## 可复现安装和网页接入

```powershell
cd D:\MYCODE\APP\fishbot\qq-ai-saas
python scripts/napcat-setup.py
# 重新启动平台API以加载本轮适配代码；网页创建bot，选择NapCat，点击获取二维码。
```

默认运行环境 `references/napcat-windows-node`。自动生成 `.local/napcat/<bot>.json`，管理密码随机，每bot独立协议端口。无需维护者手动制作 launcher 或填写每bot endpoint。`NAPCAT_RUNTIME_DIR` 可覆盖安装目录；当前自动安装器是Windows x64版本，Linux安装包尚未实机验证，不会假装Windows包可用于Linux。

官方版本固定 [NapCat v4.18.41](https://github.com/NapNeko/NapCatQQ/releases/tag/v4.18.41)，便携 `NapCat.Shell.Windows.Node.zip` 包含 Node22.11.0 与 QQ native。安装器验证发布元数据的 SHA256 `51fbb10e7bcd56b0c2a6de86c37811e7518330e48a137ac689ab1e4d8627d3f0`，防止失败下载被当作可执行文件；没有安装系统服务、修改注册表或调用已有桌面QQ。

独立真实实验入口：

```powershell
$env:LAB_WAIT_SECONDS='900'
npm run protocol:lab -- napcat
```

文件 `.local/protocol-lab/qr.png` 由真实管理API返回的QQ二维码内容生成；对应上游缓存原PNG在该lab机器人 `qq/napcat/cache/qrcode.png`。二维码有有效期，应优先使用网页的当前二维码。`LAB_DATA_ROOT` 可选独立实验根，避免两个协议实验共用进程记录。启动期API暂不可达会等待，不再立即finally停止刚启动的QQ进程。

## Runner 与隔离

`adapters/protocol/napcat/runner.mjs`：

- 只读共享便携 Node/DLL/JS；cwd、HOME、USERPROFILE、APPDATA、LOCALAPPDATA、XDG、tmp 均落到 bot/qq；NapCat 的 `NAPCAT_WORKDIR` 指向 `bot/qq/napcat`。
- Windows native `NodeQQNTWrapperUtil.getNTUserDataInfoConfig()` 可能返回桌面QQ注册表中的真实数据根，**仅改HOME不够**。原生方法是non-configurable；本轮通过该进程 `process.dlopen` 返回对象的可写导出构造 facade，强制此方法返回独立 `bot/qq/profile`，其他原生静态方法仍绑定原对象。未知wrapper结构失败退出，不回退共享profile。没有更改上游磁盘或用户注册表。
- 实际产生原生 `profile/nt_qq/global/nt_data` 和数据库，证明真实引擎使用独立数据根。此隔离仍不是不同OS身份的恶意插件沙箱。
- 写入独立 `webui.json`，只监听127.0.0.1，随机token；写入默认和既有账号的 `onebot11*.json`，反向WS与token指向所属bot。关闭NapCat多账号守护/pipe，使平台拥有生命周期。
- 后端API认证与QR状态仍由现有NapCatAdapter代理，不向浏览器公开管理token。
- online必须 `isLogin && coreReady` 并取实际QQ身份；等待扫码不能作为在线；无QR为starting；过期/失败映射终态。启动期管理API不可达但PID存在时报告alive=true、ready=false，避免supervisor反复重启。
- 真实在线后写独立 `identity.json`；重启追加上游支持的 `-q <uin>` 尝试原生会话恢复，并与预填QQ校验。**追加真实验收：用户扫码成功后，平台启动命令重启Runner，原生会话自动恢复在线，无需再次扫码。**

## 真实资源探针

```powershell
npx tsx experiments/protocol-lab/napcat-resource.ts
```

运行1、2、4个真正的便携QQ native实例；每个必须经API返回真实二维码后才采样，进程树包括QQ native Node和原生conhost。最后样本：

| 实际未登录QQ进程数 | 总进程树RSS | 说明 |
|---:|---:|---|
| 1 | 120.80 MiB | 已有真实QR，未扫码 |
| 2 | 235.15 MiB | 独立profile和端口 |
| 4 | 471.20 MiB | 单实例约114–121MiB |

每阶段3次原始样本：`test-results/round2/napcat-prelogin-resources.json`；日志 `test-results/round2-napcat-resources.log`。不是Linux PSS、不是登录后峰值，也不包括AstrBot/API。全部资源探针实例结束后停止；单独人工扫码lab若仍存在，要通过所属ProcessHost停止。

## Euler 实际阻塞证据

`test-results/round2-euler-prelogin-attempt.log` 与 `.local/euler-prelogin-probe/bots/lab/runtime.log` 保存本轮实际进程尝试。为定位调用链，实验将签名服务指向明确不可用的本机9端口，没有伪造签名返回。

实跑到 hiro `BaseClient.fetch_qrcode → send_uni_packet('wtlogin.trans_emp') → _sign_provider → ConnectionError: Max retries exceeded`。已安装版本 `lagrange/utils/sign.py::SIGN_PKG_LIST` **包含 wtlogin.trans_emp**，所以该版本获取QR就依赖可用签名服务，并非扫码后才签名。

没有删配额、没有声称sign已实现。Euler仍需兼容当前协议版本的真实签名服务；本轮不以这条阻塞延迟NapCat真QR产品路径。Windows仅有wsl.exe壳，`wsl --status`要求安装；没有Docker命令，本分支未进行系统级Linux安装。

## 测试与偏移

新增 `tests/napcat-runner.test.ts`：独立profile/config/token/WS，拒绝越界manifest，原生不可变getter facade，启动期health、无QR、不提前online、过期状态。与现有协议契约测试、TypeScript构建检查均通过。

保留失败：第一次lab在管理API未ready时即finally停止；第二次尝试直接改native getter失败；之后改facade获得真实QR。失败日志在 `round2-napcat-lab.log`、`round2-napcat-lab-retry.log`；未删除或降低断言来隐藏错误。

追加：人工扫码在线、受控Runner进程重启后自动恢复、AstrBot/OneBot真实连接通过。同步15点采样真实在线NapCat约185.30MiB、AstrBot约200.80MiB，合计386.10MiB RSS、227.57MiB USS（不含控制面/模型Key/聊天）。证据 `test-results/round2/real-qq-start-result.json`、`complete-real-bot-summary.json`。完整扫码→回复→恢复尚缺模型Key/真实消息、整平台重启与媒体压测。生产Linux runner、硬OS隔离与四真实QQ在线验收仍未收口。
