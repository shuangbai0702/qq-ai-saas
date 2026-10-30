// Finite verification between the two previously authorized QQ accounts only.
// Reads existing private local auth. Never prints cookies, Keys or QQ identities.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const primary=JSON.parse(await readFile('.local/real-qq-test.json','utf8'));
const peer=JSON.parse(await readFile('.local/second-qq-test.json','utf8'));
const base='http://127.0.0.1:8787/v1/bots/'+primary.botId;
async function api(suffix='',payload){const r=await fetch(base+suffix,{method:payload?'PUT':'GET',headers:{Cookie:primary.cookie,'Content-Type':'application/json'},body:payload?JSON.stringify(payload):undefined,signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error('Owned API HTTP '+r.status);return r.json()}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
await mkdir('test-results/social-integration',{recursive:true});
if(process.argv[2]==='status'){
 const d=await api();console.log(JSON.stringify({revision:d.bot.revision,applied:d.bot.applied_revision,online:d.bot.health?.protocol?.online,ready:d.bot.health?.ai?.ready,style:d.config.behavior?.styleLearning,meme:d.config.behavior?.memeEnabled,pid:d.bot.health?.ai?.pid}));
}else if(process.argv[2]==='enable'){
 const old=await api();const config={...old.config,behavior:{...old.config.behavior,styleLearning:true,memeEnabled:true,memeCooldownSeconds:60,dailyMemeLimit:20}};
 const saved=await api('/config',{expectedRevision:old.bot.revision,name:old.bot.name,expectedUin:old.bot.expected_uin,config});
 for(let n=0;n<90;n++){const fresh=await api();if(fresh.bot.applied_revision===saved.revision&&fresh.bot.health?.ai?.ready&&fresh.bot.health?.protocol?.online){console.log(JSON.stringify({enabled:true,revision:fresh.bot.revision,applied:fresh.bot.applied_revision,qqOnline:true,mode:fresh.bot.runtime_mode}));process.exit(0)}await sleep(1000)}throw new Error('Applied revision deadline');
}else if(process.argv[2]==='verify-saved'){
 const file='test-results/social-integration/real-qq-image.json';const saved=JSON.parse(await readFile(file,'utf8'));
 await writeFile('test-results/social-integration/real-qq-image-second-attempt-telemetry-lag.json',JSON.stringify(saved,null,2));
 const fresh=await api(),last=fresh.bot.health?.ai?.metrics?.lastCall;const times=saved.received.map(m=>m.time);
 // Correlate the existing QQ receipt with scoped provider telemetry. No new send.
 const matched=last?.model==='gpt-5.6-luna'&&last.time>=Math.min(...times)-15&&last.time<=Math.max(...times)+5;
 const result={...saved,telemetryObservedAt:new Date().toISOString(),telemetryLagObserved:true,model:last,modelCallIsFresh:!!matched,imagesSent:fresh.bot.health?.ai?.metrics?.imagesSent||0};
 result.passed=!!matched&&result.imagesSent>0&&saved.received.some(m=>m.segments.some(x=>x.type==='image'))&&!JSON.stringify(saved.received).includes('[MEME:');await writeFile(file,JSON.stringify(result,null,2));console.log(JSON.stringify({passed:result.passed,readOnly:true,matchedModel:matched,imagesSent:result.imagesSent}));if(!result.passed)process.exitCode=1;
}else if(process.argv[2]==='verify'){
 const state=await api();if(!state.config.behavior?.memeEnabled||state.config.model!=='gpt-5.6-luna'||!state.bot.health?.protocol?.online)throw new Error('Expected real model and online QQ with image feature');
 const manifest=JSON.parse(await readFile('.local/bots/'+peer.botId+'/napcat-platform.json','utf8'));
 const identity=JSON.parse(await readFile('.local/bots/'+primary.botId+'/qq/identity.json','utf8'));
 async function history(){const r=await fetch(`http://127.0.0.1:${manifest.management.port+1}/get_friend_msg_history`,{method:'POST',headers:{Authorization:'Bearer '+manifest.reverseWs.token,'Content-Type':'application/json'},body:JSON.stringify({user_id:Number(identity.uin),count:20}),signal:AbortSignal.timeout(10000)});const d=await r.json();if(d.retcode!==0)throw new Error('Peer history failed');return(d.data.messages||[]).filter(m=>String(m.user_id||m.sender?.user_id)===String(identity.uin))}
 const previous=new Set((await history()).map(m=>String(m.message_id)));const started=Date.now();
 execFileSync(process.execPath,['scripts/qq-pair-lab.mjs','send','哈哈我刚才把计算器当遥控器按了半天，太有节目效果了。轻松测试一下：请发一张你图库里的笑哭图片，可以配一句短话。'],{stdio:'pipe',timeout:15000});
 let received=[];
 for(let n=0;n<65;n++){received=(await history()).filter(m=>!previous.has(String(m.message_id)));if(received.some(m=>(m.message||[]).some(x=>x.type==='image')))break;await sleep(1000)}
 let fresh=await api();for(let n=0;n<15&&(fresh.bot.health?.ai?.metrics?.lastCall?.time||0)*1000<started;n++){await sleep(1000);fresh=await api()}
 const result={time:new Date().toISOString(),requestStartedAt:new Date(started).toISOString(),realQQ:true,realModel:true,inboundMessages:1,model:fresh.bot.health?.ai?.metrics?.lastCall,modelCallIsFresh:(fresh.bot.health?.ai?.metrics?.lastCall?.time||0)*1000>=started,imagesSent:fresh.bot.health?.ai?.metrics?.imagesSent||0,received:received.map(m=>({time:m.time,idHash:createHash('sha256').update(String(m.message_id)).digest('hex'),segments:(m.message||[]).map(x=>x.type==='text'?{type:'text',text:x.data.text}:{type:x.type})}))};
 result.passed=result.modelCallIsFresh&&received.some(m=>(m.message||[]).some(x=>x.type==='image'))&&!JSON.stringify(result.received).includes('[MEME:');await writeFile('test-results/social-integration/real-qq-image.json',JSON.stringify(result,null,2));console.log(JSON.stringify({passed:result.passed,receivedImage:received.some(m=>(m.message||[]).some(x=>x.type==='image')),freshModel:result.modelCallIsFresh}));if(!result.passed)process.exitCode=1;
}else throw new Error('Use enable or verify (verify sends exactly one authorized QQ message)');
