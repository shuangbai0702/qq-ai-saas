# 实际生产发布凭据

源码发布提交b2a1542c317278693473c59c286e37ad71bcb807；构建来自干净独立worktree。receipt记录真实部署、私密备份摘要、加载指纹和Journal只读比对。没有秘密、聊天正文、QQ登录文件或业务dump进入此目录。

operators是本次本地.local/b-release-20261113操作脚本的留存；其运行位置仍要求仓库根目录的.local/b-release-20261113，并复用既有私密SSH配置，不能把本目录当作独立免配置部署器。

## 回退

当前Agent6672仍持有发布前owner和函数引用。需要回退时从仓库根运行`python .local/b-release-20261113/rollback.py`；该入口先捕获新baseline，以hold-rollback取原引用并fence，再按transaction的before/after哈希恢复程序和媒体配置、恢复owner、重载AI、按PID+创建时间重启API并恢复运行。不会还原业务数据库，不丢弃发布后消息。若Agent已重启或引用不存在，会拒绝开始；需重新核对进程身份和制定受控回退，不能绕过所有权检查。

部署中两次备份门禁失败后的恢复原运行状态已实际成功；最终发布后完整程序回退未在线演练。hold-rollback扩展经过审阅和语法检查，未执行。业务恢复备份保存在receipt中列出的受限目录；不能盲目覆盖已有Journal、Goal、Action、ToolEffects或QQ状态。
