// Finite, explicitly authorized two-account lab only. No arbitrary recipient.
import {readFile,writeFile,mkdir} from 'node:fs/promises';import {execFileSync} from 'node:child_process';import {createHash} from 'node:crypto';
const info=JSON.parse(await readFile('.local/real-qq-test.json','utf8'));const peer=JSON.parse(await readFile('.local/second-qq-test.json','utf8'));
const api=async(route='',body,method)=>{const r=await fetch('http://127.0.0.1:8787/v1/bots/'+info.botId+route,{method:method||(body?'POST':'GET'),headers:{Cookie:info.cookie,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error('Local owned-bot API failed: '+r.status);return r.json()};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));const mode=process.argv[2];await mkdir('test-results/memory-synthesis',{recursive:true});
if(mode==='enable'){
 const d=await api();if(!d.config.behavior?.memoryAutoCapture){const saved=await api('/config',{expectedRevision:d.bot.revision,name:d.bot.name,expectedUin:d.bot.expected_uin,config:{...d.config,behavior:{...d.config.behavior,memoryAutoCapture:true}}},'PUT');let ready=false;for(let i=0;i<60;i++){const s=await api();if(s.bot.applied_revision===saved.revision&&s.bot.health?.ai?.ready&&s.bot.health?.protocol?.online){ready=true;break}await sleep(1000)}if(!ready)throw new Error('Applied configuration deadline')}
 const s=await api();console.log(JSON.stringify({enabled:true,revision:s.bot.revision,applied:s.bot.applied_revision,qqOnline:s.bot.health?.protocol?.online}));
}else if(mode==='verify'){
 const status=await api();if(!status.config.behavior?.memoryAutoCapture||status.config.model!=='gpt-5.6-luna')throw new Error('Enable capture and correct model first');
 const manifest=JSON.parse(await readFile('.local/bots/'+peer.botId+'/napcat-platform.json','utf8'));const identity=JSON.parse(await readFile('.local/bots/'+info.botId+'/qq/identity.json','utf8'));
 async function history(){const r=await fetch(`http://127.0.0.1:${manifest.management.port+1}/get_friend_msg_history`,{method:'POST',headers:{Authorization:'Bearer '+manifest.reverseWs.token,'Content-Type':'application/json'},body:JSON.stringify({user_id:Number(identity.uin),count:20}),signal:AbortSignal.timeout(10000)});const d=await r.json();if(d.retcode!==0)throw new Error('Authorized peer history failed');return(d.data.messages||[]).filter(m=>String(m.user_id||m.sender?.user_id)===String(identity.uin))}
 const results=[];
 for(const [name,text]of [['self-report','我最近都喝桂花乌龙'],['recall','我爱喝什么？请只说饮料名。']]){
  const old=new Set((await history()).map(m=>String(m.message_id)));const started=Date.now();execFileSync(process.execPath,['scripts/qq-pair-lab.mjs','send',text],{stdio:'pipe',timeout:15000});let replies=[];
  for(let i=0;i<45;i++){replies=(await history()).filter(m=>!old.has(String(m.message_id)));if(replies.length)break;await sleep(1000)}if(!replies.length)throw new Error('No QQ reply; no automatic send retries');await sleep(2000);replies=(await history()).filter(m=>!old.has(String(m.message_id)));
  let s;for(let i=0;i<12;i++){s=await api();if((s.bot.health?.ai?.metrics?.lastCall?.time||0)*1000>=started)break;await sleep(500)}
  const last=s.bot.health?.ai?.metrics?.lastCall;const entry={name,request:text,modelSucceeded:!!last&&last.time*1000>=started,replies:replies.map(m=>({text:(m.message||[]).filter(x=>x.type==='text').map(x=>x.data.text).join(''),time:m.time,messageIdHash:createHash('sha256').update(String(m.message_id)).digest('hex')})),modelCall:last};results.push(entry);await writeFile('test-results/memory-synthesis/real-qq.json',JSON.stringify({realQQ:true,realModel:true,maxInboundMessages:2,results},null,2));console.log(name+': '+entry.replies.map(x=>x.text).join(' / '));
 }
 if(!results[1].replies.some(m=>m.text.includes('桂花乌龙')))throw new Error('Real recall did not mention the observed preference; inspect evidence');
}else throw new Error('Use enable or verify. verify sends exactly two authorized test messages.');
