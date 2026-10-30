// Finite real-model budget: six inbound messages, only the authorized QQ pair.
import {readFile,writeFile} from 'node:fs/promises';import {execFileSync} from 'node:child_process';import {createHash} from 'node:crypto';
const info=JSON.parse(await readFile('.local/real-qq-test.json','utf8'));const peer=JSON.parse(await readFile('.local/second-qq-test.json','utf8'));
const manifest=JSON.parse(await readFile('.local/bots/'+peer.botId+'/napcat-platform.json','utf8'));const first=JSON.parse(await readFile('.local/bots/'+info.botId+'/qq/identity.json','utf8'));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function history(){const r=await fetch(`http://127.0.0.1:${manifest.management.port+1}/get_friend_msg_history`,{method:'POST',headers:{Authorization:'Bearer '+manifest.reverseWs.token,'Content-Type':'application/json'},body:JSON.stringify({user_id:Number(first.uin),count:20})});const d=await r.json();if(d.retcode!==0)throw new Error('QQ history unavailable');return(d.data.messages||[]).filter(m=>String(m.user_id||m.sender?.user_id)===String(first.uin));}
async function state(){return(await(await fetch('http://127.0.0.1:8787/v1/bots/'+info.botId,{headers:{Cookie:info.cookie}})).json()).bot;}
const cases=[['remember','记住：验收饮料=绿茶'],['recall-after-restart','刚才让你长期记住的验收饮料是什么？一句话回答。'],['correct','更正记忆：验收饮料=红茶'],['recall-correction','现在长期记忆里的验收饮料是什么？不要使用旧的值。'],['capability','现在给我搜索B站并截图发来。如果没有搜索和截图工具，请直接说做不到，不要假装做过。'],['emotion','谢谢你，今天做得好。用你自己的口吻说一句，别像客服。']];
const resume=process.argv.includes('--resume');const results=resume?JSON.parse(await readFile('test-results/round3/real-scenarios.json','utf8')).results:[];
for(const [name,text]of cases){if(results.some(x=>x.name===name))continue;
 if(name==='recall-after-restart'&&!resume)execFileSync(process.execPath,['scripts/round3-live.mjs','restart-first'],{stdio:'pipe',timeout:90000});
 const before=new Set((await history()).map(m=>String(m.message_id)));const started=Date.now();execFileSync(process.execPath,['scripts/qq-pair-lab.mjs','send',text],{stdio:'pipe',timeout:15000});
 let reply;for(let i=0;i<60;i++){reply=(await history()).find(m=>!before.has(String(m.message_id)));if(reply)break;await sleep(1000)}if(!reply)throw new Error('No real QQ reply for '+name);
 let bot;for(let i=0;i<12;i++){bot=await state();if((bot.health?.ai?.metrics?.lastCall?.time||0)*1000>=started)break;await sleep(500)}
 const record={name,request:text,reply:(reply.message||[]).filter(x=>x.type==='text').map(x=>x.data.text).join(''),qqTime:reply.time,qqMessageHash:createHash('sha256').update(String(reply.message_id)).digest('hex'),runtimeMode:bot.runtime_mode,groupId:bot.group_id,aiPid:bot.health?.ai?.pid,protocolOnline:bot.health?.protocol?.online,modelCall:bot.health?.ai?.metrics?.lastCall};results.push(record);console.log(name+': '+record.reply);
 await writeFile('test-results/round3/real-scenarios.json',JSON.stringify({startedAt:new Date(started).toISOString(),realQQ:true,realModel:true,maxMessages:6,results},null,2));
}
