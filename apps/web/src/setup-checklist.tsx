import {runtimeStatus} from '../../../packages/shared/runtime-status';

export function SetupChecklist({bot,hasKey,enabled}:{bot:any;hasKey:boolean;enabled:boolean}){
 const runtime=runtimeStatus(bot);
 if(hasKey&&runtime.online&&runtime.aiReady&&enabled)return null;
 const base='#/bots/'+bot.id;
 const steps=[
  {done:hasKey,title:'配置模型与 API Key',href:'/config/model',action:'配置模型',hint:'可以先连接 QQ，填写 API Key 后才能生成 AI 回复。'},
  {done:runtime.online,title:'启动并连接 QQ',href:'/qq',action:'前往 QQ 连接',hint:'先尝试恢复已保存的会话，需要扫码时会显示二维码。'},
  {done:enabled&&runtime.aiReady,title:enabled?'等待 AI 核心就绪':'开启聊天',href:enabled?'/logs':'/config/chat',action:enabled?'查看运行日志':'打开聊天设置',hint:enabled?'核心就绪后，私聊发一句“你好”即可试聊。':'聊天当前已关闭，可以在接收与群参与设置中开启。'}
 ];
 const next=steps.findIndex(step=>!step.done);
 return <section className="panel setup-checklist" aria-label="机器人上手检查">
  <h2>接下来做什么</h2>
  <ol>{steps.map((step,index)=><li key={step.href} aria-current={index===next?'step':undefined}>
   <b>{step.done?'✓ ':''}{step.title}</b>
   {index===next&&<><p>{step.hint}</p><a href={base+step.href}>{step.action}</a></>}
   {!step.done&&index!==next&&<a href={base+step.href}>{step.action}</a>}
  </li>)}</ol>
 </section>;
}
