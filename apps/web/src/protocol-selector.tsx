import {useEffect,useState} from 'react';
import {api} from './api';
import {useLifetime} from './hooks';
export function ProtocolSelector({id,bot,blocked,onSaved}:{id:string;bot:any;blocked:boolean;onSaved:()=>Promise<unknown>}){
 const [selected,setSelected]=useState(bot.protocol),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const owner=useLifetime();
 useEffect(()=>{setSelected(bot.protocol)},[bot.protocol]);
 if(!['napcat','llbot'].includes(bot.protocol))return null;
 const stopped=bot.desired_state==='stopped'&&bot.observed_state==='stopped';
 async function save(){
  const page=owner.current;if(!page||page.signal.aborted||busy||blocked||!stopped)return;
  setBusy(true);setMessage('');
  try{const result=await api('/bots/'+id+'/protocol',{protocol:selected,expectedRevision:bot.revision,expectedGeneration:bot.generation},'PUT',page.signal);
   if(page.signal.aborted)return;setMessage(result.changed?'切换已排队。等待状态更新为所选协议后，再连接同一个QQ。':'当前已经是所选协议。');await onSaved();
  }catch(error){if(!page.signal.aborted)setMessage((error as Error).message)}finally{if(!page.signal.aborted)setBusy(false)}
 }
 return <section className="panel"><div className="section-heading"><h2>QQ协议</h2><p>当前使用 {bot.protocol==='llbot'?'LLBot':'NapCat'}。切换保留机器人、角色卡和长期记忆；需要重新连接原QQ。</p></div><label className="field"><span>选择协议后端</span><select value={selected} onChange={e=>setSelected(e.target.value)} disabled={busy||blocked||!stopped}><option value="napcat">NapCat</option><option value="llbot">LLBot</option></select></label>{!stopped&&<p className="muted">请先停止机器人，等待停止完成后再切换。</p>}<button type="button" className="primary" disabled={busy||blocked||!stopped||selected===bot.protocol} onClick={()=>void save()}>{busy?'正在提交…':'切换QQ协议'}</button>{message&&<p role="status">{message}</p>}{bot.last_error&&<p role="alert">{bot.last_error}</p>}</section>;
}
