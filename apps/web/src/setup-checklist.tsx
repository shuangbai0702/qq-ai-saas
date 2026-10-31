import {runtimeStatus} from '../../../packages/shared/runtime-status';
export function SetupChecklist({bot,hasKey,enabled}:{bot:any;hasKey:boolean;enabled:boolean}){
 const r=runtimeStatus(bot);if(hasKey&&r.online&&r.aiReady&&enabled)return null;
 const base='#/bots/'+bot.id;
 return <section className="panel setup-checklist" aria-label="机器人上手检查"><h2>接下来做什么</h2><ol>
 <li><b>{hasKey?'✓ 模型 Key 已保存':'1. 配置模型与 Key'}</b><p>{hasKey?'已保存不代表模型可用，建议在模型配置中测试连接。':'没有 Key 也能连接 QQ，但无法生成 AI 回复。'}</p><a href={base+'/config/profile'}>检查模型配置</a></li>
 <li><b>{r.online?'✓ QQ 已连接':'2. 启动并连接 QQ'}</b><p>{r.online?'无需重复扫码；已有会话会尽量自动恢复。':'进入 QQ 连接页启动。能恢复旧会话就直接上线，否则使用手机 QQ 扫码并确认。'}</p><a href={base+'/qq'}>查看 QQ 连接</a></li>
 <li><b>{!enabled?'3. 聊天开关已关闭':r.aiReady?'✓ AI 核心就绪':'3. 等待 AI 就绪并试聊'}</b><p>{!enabled?'先在能力管理打开聊天总开关。':r.aiReady?'私聊发一句“你好”；群聊按触发规则 @机器人。QQ 在线不代表模型一定可用。':'启动后等待真实就绪回执。若出现错误，查看运行日志；不要连续重启。'}</p><a href={base+(!enabled?'/config/capabilities':'/logs')}>{!enabled?'打开聊天能力':'查看运行日志'}</a></li>
 </ol></section>
}
