# Docker 部署

适用 `linux/amd64`，Docker Engine + Compose v2 或更高版本。镜像内包含编译后的 Web/API、PGlite、固定版本 AstrBot 及 Linux NapCat；不用另装 Python、Node 或为每个机器人再启动 Web 服务。Euler 签名实验环境未打包。

## 使用预构建镜像

将交付包中的 `compose.yaml` 和 `docker/` 放在同一目录层级。首次部署解压到新目录，勿覆盖已有配置。**新目录不等于新数据卷**：默认 Compose 项目名为 `fishbot-saas`。同机已有该项目/卷时，启动脚本拒绝重新初始化；请回原部署目录启动或恢复成对的配置文件。运营方需要额外部署时，应显式规划独立 Compose 项目名、端口和数据卷。仅修改端口不足以隔离数据。

```sh
docker load -i fishbot-qq-ai-saas_0.1.0_linux-amd64.tar.gz
sh docker/up.sh owner@example.com http://127.0.0.1:8787
docker compose --env-file .env.docker ps
```

Windows Docker Desktop 使用 Linux 容器：

```powershell
docker load -i fishbot-qq-ai-saas_0.1.0_linux-amd64.tar.gz
.\docker\up.ps1 -Email owner@example.com -Origin http://127.0.0.1:8787
```

初始管理员密码保存在部署目录 `.local/docker-secrets/admin-password`。脚本不打印密码；`.env.docker` 不保存密码。重复启动保留现有管理员，不自动重置密码或把普通账号提升为管理员。缺一份初始化文件时脚本报错，要求先恢复配对文件。

默认仅发布 `127.0.0.1:8787`。本机浏览器打开该地址；远程初验可通过 SSH 转发同一端口。对外使用 HTTPS 反向代理，将 `.env.docker` 的 `APP_ORIGIN` 设为浏览器访问的完整 origin，`COOKIE_SECURE=true`，再执行 `docker compose --env-file .env.docker up -d`。代理连本机8787即可，不要公开 AstrBot/NapCat 管理端口。

管理员登录后在“激活码”页面发码；默认一次注册、每账号一个机器人。客户注册后自行设置人格、Base URL、Key 与模型 ID，选择 NapCat 获取真实二维码。选择“模拟协议”会明确显示模拟状态，不能扫码登录真实 QQ。

## 从源码构建

在完整开发项目中安装 npm 依赖、准备 `references/astrbot` 固定源码及 **空的** PGlite 模板，然后：

```sh
npm run docker:prepare -- .local/docker-context-release
docker build -t fishbot/qq-ai-saas:0.1.0 .local/docker-context-release
```

构建上下文仅复制白名单文件，拒绝符号链接，逐文件记录 SHA-256。不会打包现有 `.local`、Master Key、QQ 会话、用户图库或模型密钥。Linux 原生加载器按固定提交下载并校验 SHA-256；基础镜像以 digest 固定，AstrBot 以 `uv.lock` 安装。

构建阶段 TypeScript 需要比运行阶段更多内存；小服务器优先 `docker load`。镜像与依赖版本见 `Dockerfile`、`docker/napcat-upstream.json` 及发布清单。尚未验证 ARM，不能将此镜像称为多架构版本。

## 持久化和恢复

所有可变数据保存在 Compose 的 `fishbot-data` 卷：数据库、Master Key、QQ profile、人格配置、记忆、社交学习、图库和运行记录。镜像文件系统只读，应用 UID10001，不挂载宿主 Docker socket，不要求 privileged。共享组默认容量8，只允许平台受控插件，独立模式保留；同一 Python 进程不构成任意插件代码的安全沙箱。

```sh
# 优雅停止，保留卷和会话
docker compose --env-file .env.docker stop
# 继续运行并按持久化 desired state 恢复
docker compose --env-file .env.docker up -d
# 查看脱敏进程日志
docker compose --env-file .env.docker logs --tail 100 platform
```

备份前先停止服务，完整保存数据卷与部署目录的初始化配置；必须同时保留数据库和 Master Key。不要执行 `down -v`、删除卷或只备份数据库。数据库已有加密凭据时，`master.key` 缺失或损坏会拒绝启动，须恢复原文件；生成新密钥不能恢复旧凭据。意外退出后锁有20秒失效窗口，启动会等待旧锁释放；不要手工删除仍被运行服务使用的锁，也不要让两个容器共享打开同一个嵌入式数据库。

模型 URL 默认拒绝本机、内网、保留地址、混合 DNS 和重定向，并在实际模型 SDK 连接处再次验证。运营方确有本地模型服务时，可在 Compose environment 显式设置 `FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS` 为逗号分隔的精确 origin；普通客户配置不能授权该例外。Windows TUN/fake-IP 的特例只写在本机 `.env`，不会进入镜像。
