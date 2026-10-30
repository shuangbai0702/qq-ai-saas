import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile,writeFile,mkdir} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(fn:()=>Promise<boolean>,message:string,timeout=90000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await sleep(1000)}throw new Error('Timeout: '+message)}
test('four real AstrBot processes with mock QQ, configuration, QR and lifecycle isolation',{timeout:300000},async t=>{
 await mkdir('.local',{recursive:true});await mkdir('test-results',{recursive:true});const root=await mkdtemp(path.resolve('.local/runtime-test-'));const {app,db,agent}=await createApp({root});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,4),email:'runtime@example.com',name:'实例测试',password:'runtime-test-123'}});const cookie=auth.cookies[0].value;
 const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies:{fish_session:cookie}});
 const ids:string[]=[];
 for(let i=0;i<4;i++){const r=await call('POST','/v1/bots',{name:`运行测试${i}`,protocol:'mock',runtimeMode:'dedicated',expectedUin:String(90001000+i),config:{...defaultConfig,systemPrompt:`独立人格 ${i}`}});assert.equal(r.statusCode,201,r.body);ids.push(r.json().id)}
 for(const id of ids)await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start'});
 await until(async()=>{const bots=(await call('GET','/v1/bots')).json().items;return bots.length===4&&bots.every((b:any)=>b.health?.ai?.ready&&b.health?.protocol?.alive)},'four AstrBot initialized',160000);
 const bots=(await call('GET','/v1/bots')).json().items;
 await writeFile('test-results/runtime-measurements.json',JSON.stringify({root,os:process.platform,phase:'four idle real AstrBot + four mock QQ processes, before lifecycle tests',measures:bots.map((b:any)=>({id:b.id,health:b.health}))},null,2));
 assert.equal(new Set(bots.map((b:any)=>b.health.ai.pid)).size,4);assert.ok(bots.every((b:any)=>b.applied_revision===1));
 for(let i=0;i<ids.length;i++){const cfg=JSON.parse((await readFile(path.join(root,'bots',ids[i],'ai/data/cmd_config.json'),'utf8')).replace(/^\uFEFF/,''));assert.equal(cfg.dashboard.enable,false);assert.equal(await readFile(path.join(root,'bots',ids[i],'ai/persona.txt'),'utf8'),`独立人格 ${i}`)}
 const sid=(await call('POST',`/v1/bots/${ids[0]}/login-sessions`,{})).json().id;
 await until(async()=>(await call('GET',`/v1/login-sessions/${sid}`)).json().hasQr,'QR ready');assert.equal((await call('GET',`/v1/login-sessions/${sid}/qr`)).headers['content-type'],'image/png');
 await call('POST',`/v1/bots/${ids[0]}/mock-transition`,{state:'waiting_confirm'});await until(async()=>(await call('GET',`/v1/login-sessions/${sid}`)).json().state==='waiting_confirm','waiting confirm');
 await call('POST',`/v1/bots/${ids[0]}/mock-transition`,{state:'online',uin:'90001000'});await until(async()=>(await call('GET',`/v1/login-sessions/${sid}`)).json().state==='online','mock online binding');
 const other=ids[1];const sid2=(await call('POST',`/v1/bots/${other}/login-sessions`,{})).json().id;await until(async()=>(await call('GET',`/v1/login-sessions/${sid2}`)).json().hasQr,'second QR');
 await call('POST',`/v1/bots/${other}/mock-transition`,{state:'online',uin:'90001000'});await until(async()=>(await call('GET',`/v1/login-sessions/${sid2}`)).json().state==='failed','duplicate or unexpected QQ rejected');
 const save=await call('PUT',`/v1/bots/${ids[0]}/config`,{expectedRevision:1,name:'更新后的实例',expectedUin:'90001000',config:{...defaultConfig,systemPrompt:'更新后只属于0的人格'},apiKey:'synthetic-runtime-key'});assert.equal(save.statusCode,200,save.body);
 await until(async()=>(await call('GET',`/v1/bots/${ids[0]}`)).json().bot.applied_revision===2,'revision acknowledged');
 const generated=await readFile(path.join(root,'bots',ids[0],'ai/data/cmd_config.json'),'utf8');assert.ok(generated.includes('$FISHBOT_MODEL_KEY'));assert.ok(!generated.includes('synthetic-runtime-key'));
 const siblingPid=(await call('GET',`/v1/bots/${ids[2]}`)).json().bot.health.ai.pid;
 const crashedPid=(await agent.host.inspect(ids[3],'ai')).pid;process.kill(crashedPid);
 await until(async()=>{const p=await agent.host.inspect(ids[3],'ai');const b=(await call('GET',`/v1/bots/${ids[3]}`)).json().bot;return !!p&&p.pid!==crashedPid&&b.health?.ai?.ready},'unexpected crash automatically recovered');
 await call('POST',`/v1/bots/${ids[0]}/actions`,{action:'stop',idempotencyKey:'stop'});await until(async()=>!(await agent.host.inspect(ids[0],'ai')),'stop exits');assert.equal((await agent.host.inspect(ids[2],'ai')).pid,siblingPid);
 await call('POST',`/v1/bots/${ids[0]}/actions`,{action:'start',idempotencyKey:'restore'});await until(async()=>{const b=(await call('GET',`/v1/bots/${ids[0]}`)).json().bot;return b.health?.ai?.ready&&b.health?.protocol?.online},'mock session restored');
 const measures=(await call('GET','/v1/bots')).json().items.map((b:any)=>({id:b.id,health:b.health}));await writeFile('test-results/lifecycle-final.json',JSON.stringify({root,os:process.platform,note:'Real AstrBot, mock QQ; RSS includes each subprocess tree; not Linux capacity',measures},null,2));
 const previousPids=(await call('GET','/v1/bots')).json().items.map((b:any)=>b.health?.ai?.pid);await app.close();
 const restored=await createApp({root});t.after(()=>restored.app.close());
 await until(async()=>{const r=await restored.app.inject({method:'GET',url:'/v1/bots',cookies:{fish_session:cookie}});const items=r.json().items;return r.statusCode===200&&items.length===4&&items.every((b:any)=>b.health?.ai?.ready&&!previousPids.includes(b.health.ai.pid))},'application restart restores desired running bots and persisted login session',160000);
});
