import {qqExternalBudget,externalCount,biliWorkEffects} from '../packages/database/external-action-budget.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {ToolEffects} from '../packages/database/tool-effects.js';
import {BilibiliExecutor} from '../apps/node-agent/src/bilibili/executor.js';
import {BiliWeb,boundedJson,cookieCandidate,BiliError} from '../apps/node-agent/src/bilibili/web-adapter.js';
import {bridgeToken} from '../packages/shared/tool-bridge.js';
import {BiliAccounts} from '../apps/node-agent/src/bilibili/account-broker.js';
import {BilibiliConfigSchema} from '../packages/contracts/bilibili.js';

const json=(data:any,headers?:HeadersInit)=>new Response(JSON.stringify({code:0,data}),{headers});
const barrier=()=>{let release!:()=>void;const promise=new Promise<void>(resolve=>release=resolve);return{promise,release}};
const signingNav={wbi_img:{img_url:'https://i0.hdslb.com/bfs/wbi/0123456789abcdef0123456789abcdef.png',sub_url:'https://i0.hdslb.com/bfs/wbi/fedcba9876543210fedcba9876543210.png'}};

test('analysis admission rejects missing model before insertion and preserves configured identity and scope fences',async t=>{
 const f=await fixture(t),origin={scope:'private:768687099',eventId:'original',objective:'inspect frames'};
 await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:true,allowAnonymous:true,scopes:['public.read','media.analyze']});
 const call=f.call('analyze',{bvid:'BV1xx411c7mD',question:'inspect frames'},{origin});
 await assert.rejects(()=>f.repo.admit(f.bot,call),(e:any)=>e.code==='model_not_configured');
 assert.equal((await f.db.query('SELECT id FROM tool_effects WHERE bot_id=$1',[f.id])).length,0);
 assert.equal((await f.app.inject({method:'GET',url:'/v1/bots/'+f.id+'/bilibili',cookies:f.cookies})).json().modelConfigured,false);
 const headers={authorization:'Bearer '+bridgeToken(f.key,f.id,f.bot.generation,f.bot.revision)};
 const rejected=await f.app.inject({method:'POST',url:'/internal/v1/bots/'+f.id+'/tools',headers,payload:call});
 assert.equal(rejected.statusCode,409);assert.equal(rejected.json().error.code,'model_not_configured');assert.equal(rejected.json().receipt.status,'rejected');assert.equal(rejected.json().execution,'failed');
 await f.repo.configure(f.bot.tenant_id,f.id,3,{enabled:true,scopes:['public.read','media.analyze'],model:{provider:'bot'}});
 const row=await f.repo.admit(f.bot,call);assert.equal((await f.repo.admit(f.bot,call)).id,row.id);
 assert.equal((await f.app.inject({method:'GET',url:'/v1/bots/'+f.id+'/bilibili',cookies:f.cookies})).json().modelConfigured,true);
 await assert.rejects(()=>f.repo.admit(f.bot,{...call,origin:{...origin,scope:'private:999999'}}),/身份或会话不一致/);
 await f.db.query('DELETE FROM secrets WHERE bot_id=$1',[f.id]);
 assert.equal((await f.repo.admit(f.bot,call)).id,row.id,'same admitted action remains queryable without a new model dispatch');
 await assert.rejects(()=>f.repo.admit(f.bot,{...call,stepId:'new',args:{...call.args,bvid:'BV1xx411c7mE'}}),(e:any)=>e.code==='model_not_configured');
 await f.repo.configure(f.bot.tenant_id,f.id,4,{enabled:true,scopes:['public.read','media.analyze'],model:{provider:'openai',baseUrl:'https://fixture.invalid/v1',model:'fixture-image'}});
 await assert.rejects(()=>f.repo.admit(f.bot,{...call,stepId:'independent'}),(e:any)=>e.code==='model_not_configured');
 await f.repo.configure(f.bot.tenant_id,f.id,5,{enabled:true,scopes:['public.read','media.analyze'],model:{provider:'openai',baseUrl:'https://fixture.invalid/v1',model:'fixture-image'}},'encrypted-fixture');
 assert.ok((await f.repo.admit(f.bot,{...call,stepId:'independent'})).id);
 await f.repo.configure(f.bot.tenant_id,f.id,6,{enabled:true,scopes:['public.read'],model:{provider:'openai',baseUrl:'https://fixture.invalid/v1',model:'fixture-image'}});
 await assert.rejects(()=>f.repo.admit(f.bot,{...call,stepId:'unauthorized'}),/未授权/);
});

test('unresolved exact analysis reuses responsibility across model steps while changed observations remain legal',async t=>{
 const f=await fixture(t);
 await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:true,scopes:['public.read','media.analyze'],model:{provider:'bot'}});
 const args={bvid:'BV1xx411c7mD',question:'inspect evidence'},origin={scope:'private:768687099',eventId:'original',objective:'inspect evidence'};
 const first=await f.repo.admit(f.bot,f.call('analyze',args,{origin}));
 for(const state of ['queued','claimed','dispatching','unknown']){
  await f.db.query('UPDATE tool_effects SET state=$2 WHERE id=$1',[first.id,state]);
  const repeated=await f.repo.admit(f.bot,f.call('analyze',args,{origin,stepId:'new-step-'+state}));
  assert.equal(repeated.id,first.id);assert.equal(repeated.state,state);
 }
 const changed=await f.repo.admit(f.bot,f.call('analyze',{...args,question:'inspect a different detail'},{origin,stepId:'new-question'}));
 assert.notEqual(changed.id,first.id);
 const independent=await f.repo.admit(f.bot,f.call('analyze',args,{origin,workId:'independent-responsibility',stepId:'independent'}));
 assert.notEqual(independent.id,first.id);
 await f.db.query("UPDATE tool_effects SET state='finished' WHERE id=$1",[first.id]);
 const fresh=await f.repo.admit(f.bot,f.call('analyze',args,{origin,stepId:'fresh-after-confirmation'}));
 assert.notEqual(fresh.id,first.id,'completed analysis does not prohibit a later deliberate observation');
});

test('identical cross-Work searches reuse original receipts without refreshing age or crossing audience and account fences',async t=>{
 const f=await fixture(t);let searches=0;
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async url=>String(url).includes('/nav')?json(signingNav):(searches++,json({result:[{bvid:'BV1xx411c7mD',title:'original evidence',pic:'https://i0.hdslb.com/bfs/archive/fixture.jpg'}]}))));
 const run=async(work:string,args:any={query:'same topic'},scope='group:963105233')=>{
  const row=await f.repo.admit(f.bot,f.call('search',args,{workId:work,origin:{scope,eventId:work,objective:'actual query'}}));
  await executor.execute(await f.repo.claim('reuse-reader'),new AbortController().signal);return(await f.repo.get(f.bot.tenant_id,f.id,row.id)).result;
 };
 const original=await run('first');
 const cycle='10000000-0000-4000-8000-000000000001',goal='10000000-0000-4000-8000-000000000002',work='10000000-0000-4000-8000-000000000003';
 await f.db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES($1,$2,$3,1,1,1,'fixture','executing','{}')",[cycle,f.id,f.bot.tenant_id]);
 await f.db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,source) VALUES($1,$2,$3,'owner_request','active','actual source','delegated',$4)",[goal,f.id,f.bot.tenant_id,JSON.stringify({scope:'group:963105233'})]);
 await f.db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,goal_id) VALUES($1,$2,$3,$4,1,'research','','actual query','completed',now()+interval '1 hour',$5)",[work,cycle,f.id,f.bot.tenant_id,goal]);
 await f.db.query("UPDATE tool_effects SET authority='autonomous',origin_scope='',work_id=$2 WHERE id=$1",[original.actionId,work]);
 assert.equal(original.data.videos[0].cover,'https://i0.hdslb.com/bfs/archive/fixture.jpg');
 const next=await run('second');assert.equal(searches,1);assert.equal(next.data.reused.actionId,original.actionId);assert.equal(next.evidence.at,original.evidence.at);
 await f.db.query("INSERT INTO tool_service_cooldowns(service,until_at,reason) VALUES('bilibili',now()+interval '15 minutes','fixture')");
 const duringCooldown=await run('cooldown');assert.equal(duringCooldown.execution,'completed');assert.equal(duringCooldown.data.reused.actionId,original.actionId);assert.equal(searches,1);
 await f.db.query('DELETE FROM tool_service_cooldowns');
 await run('another-group',{query:'same topic'},'group:88888888');assert.equal(searches,2);
 await run('changed-query',{query:'different topic'});assert.equal(searches,3);
 await f.db.query("UPDATE tool_effects SET updated_at=now()-interval '3 minutes' WHERE id=$1",[original.actionId]);
 const expired=await run('expired');assert.equal(searches,4);assert.equal(expired.data.reused,undefined,'reused copies cannot extend the original TTL');
 await f.db.query('UPDATE bilibili_accounts SET revision=revision+1 WHERE bot_id=$1',[f.id]);
 await run('rotated');assert.equal(searches,5,'new credential revision cannot reuse old account result');
 await run('force-fresh',{query:'same topic',fresh:true});assert.equal(searches,6,'explicit freshness can bypass reuse but not authorization or cooldown');
 assert.equal(await f.repo.recentRead({operation:'comment'}),null,'writes never participate');
});

test('WBI signing cache expires, isolates rotated credentials and remains bounded',async t=>{
 let now=1793778000000,navs=0;const requests:URL[]=[];
 t.mock.method(Date,'now',()=>now);
 const web=new BiliWeb(async(url)=>{const u=new URL(String(url));requests.push(u);if(u.pathname.endsWith('/nav')){navs++;return json(signingNav)}return json({result:[]})});
 const credential=(session:string)=>({cookies:{DedeUserID:'123',SESSDATA:session,bili_jct:'csrf'},refreshToken:''});
 const run=(c?:ReturnType<typeof credential>)=>web.signed('/x/web-interface/wbi/search/type',{search_type:'video',keyword:'电饭锅 煮饭!',page:'2'},c);
 await run(credential('one'));now+=2000;await run(credential('one'));assert.equal(navs,1);
 assert.notEqual(requests[1].searchParams.get('wts'),requests[2].searchParams.get('wts'),'each request has a fresh signature timestamp');
 await run(credential('two'));await run();assert.equal(navs,3,'new sessions and anonymous requests do not share cache entries');
 now+=5*60*1000;await run(credential('one'));assert.equal(navs,4,'expired material is refreshed');
 for(let i=0;i<9;i++)await run(credential('bounded-'+i));
 const previous=navs;await run(credential('one'));assert.equal(navs,previous+1,'oldest material was evicted');
});

test('WBI signing rejects malformed or late canceled nav and never retries risk responses',async()=>{
 const entered=barrier(),resume=barrier(),controller=new AbortController();let navs=0,searches=0;
 const web=new BiliWeb(async(url)=>{if(String(url).includes('/nav')){navs++;if(navs===1){entered.release();await resume.promise}return json(signingNav)}searches++;return new Response('',{status:412})});
 const args={keyword:'test'},pending=web.signed('/x/web-interface/wbi/search/type',args,undefined,controller.signal);
 await entered.promise;controller.abort();resume.release();await assert.rejects(()=>pending);assert.equal(searches,0);
 await assert.rejects(()=>web.signed('/x/web-interface/wbi/search/type',args),(e:any)=>e.code==='risk_control'&&e.source?.httpStatus===412);
 assert.equal(navs,2,'late canceled nav did not populate the cache');assert.equal(searches,1,'no unsigned retry or anonymous fallback');
 let invalidCalls=0;const invalid=new BiliWeb(async()=>{invalidCalls++;return json({wbi_img:{img_url:'broken',sub_url:'broken'}})});
 for(let i=0;i<2;i++)await assert.rejects(()=>invalid.signed('/x/web-interface/wbi/search/type',args),(e:any)=>e.code==='wbi_unavailable');
 assert.equal(invalidCalls,2,'invalid signing material is not cached');
});

test('video searches use authenticated WBI requests and reuse only signing material',async t=>{
 const f=await fixture(t),requests:URL[]=[];
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(url,options)=>{
  const u=new URL(String(url));requests.push(u);assert.match((options?.headers as any).Cookie,/SESSDATA=SESSION-SECRET/);
  if(u.pathname.endsWith('/nav'))return json(signingNav);
  assert.equal(u.pathname,'/x/web-interface/wbi/search/type');assert.equal(u.searchParams.get('search_type'),'video');assert.match(u.searchParams.get('w_rid')||'',/^[a-f0-9]{32}$/);
  return json({result:[{bvid:'BV1xx411c7mD',title:'真实搜索候选'}]});
 }));
 for(let page=1;page<=2;page++){
  const row=await f.repo.admit(f.bot,f.call('search',{query:'电饭锅 煮饭',page},{stepId:'page-'+page}));
  await executor.execute(await f.repo.claim('signed-reader'),new AbortController().signal);
  const result=(await f.repo.get(f.bot.tenant_id,f.id,row.id)).result;assert.equal(result.execution,'completed');assert.equal(result.data.videos[0].title,'真实搜索候选');
  assert.equal(requests.at(-1)?.searchParams.get('page'),String(page));assert.equal(requests.at(-1)?.searchParams.get('keyword'),'电饭锅 煮饭');
 }
 assert.equal(requests.length,3,'two searches need one nav, without caching search results across Work');
});

test('executor stop fences both delayed publication and a late media claim',async()=>{
 for(const boundary of ['publication','claim'] as const){
  const entered=barrier(),resume=barrier(),executor=new BilibiliExecutor({} as any,'unused',Buffer.alloc(32));
  let claims=0;const executions:boolean[]=[];
  executor.publishBridges=async()=>{if(boundary==='publication'){entered.release();await resume.promise}};
  executor.repo.claim=async()=>{claims++;entered.release();await resume.promise;return{operation:'analyze'} as any};
  executor.execute=async(_job,signal)=>{executions.push(signal.aborted)};
  executor.tick();await entered.promise;const stopping=executor.stop();resume.release();await stopping;
  assert.equal(claims,boundary==='claim'?1:0);
  assert.deepEqual(executions,boundary==='claim'?[true]:[],'late claimed work must settle with cancellation');
  assert.equal(executor.mediaBusy,undefined);executor.tick();assert.equal(executor.busy,undefined);
 }
});

async function fixture(t:any){
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-bili-')),value=await createApp({root,agent:false});t.after(async()=>{await value.app.close();await rm(root,{recursive:true,force:true})});
 const register=async(email:string)=>{const r=await value.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(value.db,3),email,password:'test-password-123',name:'tester'}});assert.equal(r.statusCode,200,r.body);return{fish_session:r.cookies[0].value}};
 const cookies=await register('bili-a@example.com'),other=await register('bili-b@example.com');
 const create=await value.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'bili bot',protocol:'mock',apiKey:'fixture-only-key'}});assert.equal(create.statusCode,201,create.body);
 const id=create.json().id,key=Buffer.from((await readFile(path.join(root,'master.key'),'utf8')).trim(),'hex');
 await value.db.query("UPDATE bot_config_revisions SET config=jsonb_set(config,'{behavior,ownerUin}','\"768687099\"') WHERE bot_id=$1",[id]);
 await value.db.query("UPDATE bots SET desired_state='running',health=$2 WHERE id=$1",[id,JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 const [bot]=await value.db.query('SELECT * FROM bots WHERE id=$1',[id]);
 const repo=new ToolEffects(value.db);await repo.configure(bot.tenant_id,id,1,{enabled:true,scopes:['public.read','private.read','comment.write','like.write','coin.write','notification.ack','dm.write'],dmRecipients:['123456']});
 const broker=new BiliAccounts(value.db,key,new BiliWeb());
 const credential={cookies:{SESSDATA:'SESSION-SECRET',bili_jct:'CSRF-SECRET',DedeUserID:'123'},refreshToken:'REFRESH-SECRET'};
 await value.db.query('INSERT INTO bilibili_accounts(bot_id,tenant_id,uid,label,secret) VALUES($1,$2,\'123\',\'test account\',$3)',[id,bot.tenant_id,broker.seal(bot.tenant_id,id,credential)]);
 const call=(operation:any,args:any={},patch:any={})=>({operation,args,workId:'foreground:actual-message',stepId:operation,authority:'foreground' as const,senderId:'768687099',deadlineAt:Date.now()+90000,...patch});
 return{...value,cookies,other,id,key,bot,repo,broker,credential,call};
}

test('expired tool owners cannot dispatch or renew before recovery and cannot affect the replacement owner',async t=>{
 const f=await fixture(t),row=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'lease boundary'}));
 const old=await f.repo.claim('expired-owner');
 await f.db.query("UPDATE tool_effects SET lease_until=now()-interval '1 second' WHERE id=$1",[row.id]);
 assert.equal(await f.repo.beginDispatch(old,new AbortController().signal),false);
 await assert.rejects(()=>f.repo.renew(old),/租约已失效/);
 const next=await f.repo.claim('replacement-owner');assert.equal(next.id,row.id);
 assert.equal(await f.repo.beginDispatch(old,new AbortController().signal),false);
 await assert.rejects(()=>f.repo.renew(old),/租约已失效/);
 assert.equal(await f.repo.beginDispatch(next,AbortSignal.abort()),false);
 assert.equal(await f.repo.beginDispatch(next,new AbortController().signal),true);
 assert.equal(await f.repo.beginDispatch(next,new AbortController().signal),false,'dispatch is single-owner and single-transition');
});

test('Bilibili control API preserves tenant, account, version and internal audience boundaries',async t=>{
 const f=await fixture(t),{app,db,id,cookies,other,bot,key}=f,url='/v1/bots/'+id+'/bilibili';
 const view=await app.inject({method:'GET',url,cookies});assert.equal(view.statusCode,200,view.body);assert.equal(view.json().account.uid,'123');assert.equal(view.json().mediaReady,false);assert.ok(!/SESSION-SECRET|CSRF-SECRET|REFRESH-SECRET/.test(view.body));
 assert.equal((await app.inject({method:'GET',url,cookies:other})).statusCode,404);
 assert.equal((await app.inject({method:'POST',url:url+'/login',cookies:other,payload:{}})).statusCode,404);
 assert.equal((await app.inject({method:'PUT',url,cookies,payload:{expectedRevision:1,config:{enabled:true}}})).statusCode,409);
 const internal='/internal/v1/bots/'+id+'/tools';
 assert.equal((await app.inject({method:'POST',url:internal,cookies,payload:f.call('popular')})).statusCode,401);
 const [instance]=await db.query('SELECT token FROM instances WHERE bot_id=$1',[id]);
 assert.equal((await app.inject({method:'POST',url:internal,headers:{authorization:'Bearer '+instance.token},payload:f.call('popular')})).statusCode,401);
 const headers={authorization:'Bearer '+bridgeToken(key,id,bot.generation,bot.revision)};
 const ok=await app.inject({method:'POST',url:internal,headers,payload:f.call('popular')});assert.equal(ok.statusCode,200,ok.body);
 assert.equal((await app.inject({method:'POST',url:internal,headers,payload:f.call('popular',{cookie:'malicious'})})).statusCode,400);
 await db.query('UPDATE bots SET generation=generation+1 WHERE id=$1',[id]);
 assert.equal((await app.inject({method:'POST',url:internal,headers,payload:f.call('search',{query:'fish'})})).statusCode,401);
 assert.equal((await db.query('SELECT * FROM commands')).length,0,'Bili settings did not restart QQ');
});

test('stable tool action never retries ambiguous POST, including after a new Work or restart',async t=>{
 const f=await fixture(t);let posts=0;
 const web=new BiliWeb(async(_url,options)=>{if(options?.method==='POST'){posts++;throw new Error('connection lost after accepted')}return json({})});
 const executor=new BilibiliExecutor(f.db,f.root,f.key,web),intent=f.call('comment',{aid:'170001',text:'hello'});
 const admitted=await f.repo.admit(f.bot,intent),same=await f.repo.admit(f.bot,intent);assert.equal(same.id,admitted.id);
 const job=await f.repo.claim('executor');await executor.execute(job,new AbortController().signal);
 const unknown=await f.repo.get(f.bot.tenant_id,f.id,job.id);assert.equal(unknown.result.receipt.status,'unknown');assert.equal(posts,1);
 assert.equal((await f.repo.admit(f.bot,intent)).id,job.id);
 await assert.rejects(()=>f.repo.admit(f.bot,{...intent,workId:'new-work'}),/禁止再次提交/);
 await assert.rejects(()=>f.repo.admit(f.bot,{...intent,workId:'same-effect-extra-proof',args:{...intent.args,bvid:'BV1xx411c7mD'}}),/禁止再次提交/);
 await assert.rejects(()=>f.repo.admit(f.bot,{...intent,args:{aid:'170001',text:'changed'}}),/参数不一致/);
 assert.equal(await f.repo.claim('new-executor'),null);assert.equal(posts,1);
});

test('end-to-end read: admission, lease, public API adapter, evidence, receipt and result lookup',async t=>{
 const f=await fixture(t);const requests:any[]=[];
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(url,options)=>{requests.push([String(url),options]);return json({list:[{bvid:'BV1xx411c7mD',title:'<em>fish</em>',owner:{name:'author'}}]})}));
 const job=await f.repo.admit(f.bot,f.call('popular'));await executor.execute(await f.repo.claim('reader'),new AbortController().signal);
 const result=await f.repo.get(f.bot.tenant_id,f.id,job.id);assert.equal(result.result.execution,'completed');assert.equal(result.result.receipt.status,'not_applicable');assert.equal(result.result.data.videos[0].title,'fish');assert.equal(result.result.evidence.modality,'metadata');assert.equal(requests.length,1);assert.match(requests[0][1].headers.Cookie,/SESSDATA=SESSION-SECRET/);
 await executor.publishBridges();const manifest=JSON.parse(await readFile(path.join(f.root,'bots',f.id,'ai/bilibili-bridge.json'),'utf8'));assert.ok(manifest.operations.find((s:any)=>s.operation==='popular'));assert.ok(!manifest.operations.find((s:any)=>s.operation==='analyze'));assert.ok(!JSON.stringify(manifest).includes('SESSION-SECRET'));
 await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:false});await executor.publishBridges();const disabled=JSON.parse(await readFile(path.join(f.root,'bots',f.id,'ai/bilibili-bridge.json'),'utf8'));assert.deepEqual(disabled.operations,[]);
});

test('old configuration, revoked account, untrusted sender, offline QQ and canceled job cannot dispatch',async t=>{
 const f=await fixture(t);let requests=0;const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async()=>{requests++;return json({rpid_str:'12345'})}));
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'x'},{senderId:'999999'})),/真实主人/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('dm_send',{uid:'654321',text:'x'})),/私信对象/);
 const job=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'x'}));await f.db.query('UPDATE tool_effects SET cancel_requested=true WHERE id=$1',[job.id]);assert.equal(await f.repo.claim('canceled'),null);
 const second=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'second'},{stepId:'second'}));const claimed=await f.repo.claim('worker');await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:false});await executor.execute(claimed,new AbortController().signal);assert.equal(requests,0);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,second.id)).result.receipt.status,'rejected');
 await f.db.query("UPDATE bots SET desired_state='offline' WHERE id=$1",[f.id]);await assert.rejects(()=>f.repo.admit(f.bot,f.call('popular')),/QQ必须真实在线/);
});

test('real comment ID and root/parent are retained; empty receipt remains unknown',async t=>{
 const f=await fixture(t);let body:any;const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(_url,options)=>{body=new URLSearchParams(String(options?.body));return json({rpid_str:'9007199254740999'})}));
 const row=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'reply',root:'100',parent:'200'}));await executor.execute(await f.repo.claim('writer'),new AbortController().signal);
 assert.equal(body.get('root'),'100');assert.equal(body.get('parent'),'200');const result=(await f.repo.get(f.bot.tenant_id,f.id,row.id)).result;assert.equal(result.receipt.platformObjectId,'9007199254740999');assert.equal(result.receipt.status,'confirmed');assert.equal(result.data.visibility,'not_verified');
 const empty=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async()=>json({})));const second=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'empty'},{stepId:'empty'}));await empty.execute(await f.repo.claim('empty'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,second.id)).result.receipt.status,'unknown');
});

test('QR uses Set-Cookie and verified self UID; login cancellation wins against late verification',async t=>{
 const f=await fixture(t);const headers=new Headers();for(const v of ['SESSDATA=NEW-SESSION; Path=/','bili_jct=NEW-CSRF; Path=/','DedeUserID=123; Path=/'])headers.append('set-cookie',v);
 assert.equal(cookieCandidate(headers,'new-refresh').cookies.DedeUserID,'123');assert.throws(()=>cookieCandidate(new Headers(),''),/完整登录凭证/);
 let verifyStarted!:()=>void,finishVerify!:()=>void;const entered=new Promise<void>(r=>verifyStarted=r),gate=new Promise<void>(r=>finishVerify=r);
 const web=new BiliWeb(async url=>{if(String(url).includes('/poll'))return json({code:0,refresh_token:'new-refresh'},headers);verifyStarted();await gate;return json({isLogin:true,mid:123,uname:'same account'})});
 const broker=new BiliAccounts(f.db,f.key,web);await f.db.query("INSERT INTO bilibili_logins(bot_id,tenant_id,id,secret,state,expires_at) VALUES($1,$2,'qr1',$3,'waiting_confirm',now()+interval '60 seconds')",[f.id,f.bot.tenant_id,broker.seal(f.bot.tenant_id,f.id,{key:'key',url:'https://account.bilibili.com/'},'login')]);
 const [row]=await f.db.query('SELECT * FROM bilibili_logins WHERE bot_id=$1',[f.id]);const pending=broker.poll(row,new AbortController().signal);await entered;await f.db.query("UPDATE bilibili_logins SET state='canceled',secret='' WHERE bot_id=$1",[f.id]);finishVerify();await pending;
 const [account]=await f.db.query('SELECT * FROM bilibili_accounts WHERE bot_id=$1',[f.id]);assert.equal(broker.open(f.bot.tenant_id,f.id,account.secret).cookies.SESSDATA,'SESSION-SECRET');assert.equal(account.revision,1);
 await assert.rejects(async()=>broker.open('another-tenant',f.id,account.secret));
});

test('bounded responses, subtitle CID/part, timestamps and risk control are explicit',async()=>{
 await assert.rejects(()=>boundedJson(new Response('x'.repeat(100)),32),/超过允许大小/);
 const paths:string[]=[];const web=new BiliWeb(async url=>{const u=String(url);paths.push(u);if(u.includes('/view'))return json({bvid:'BV1xx411c7mD',aid:170001,pages:[{page:1,cid:111,duration:10},{page:2,cid:222,duration:20}]});if(u.includes('/nav'))return json({wbi_img:{img_url:'https://i0.hdslb.com/bfs/wbi/'+ 'a'.repeat(32)+'.png',sub_url:'https://i0.hdslb.com/bfs/wbi/'+ 'b'.repeat(32)+'.png'}});if(u.includes('/wbi/v2'))return json({aid:170001,bvid:'BV1xx411c7mD',cid:222,subtitle:{subtitles:[{lan:'ai-zh',ai_type:0,id_str:'2064770642603168768',subtitle_url:'https://i0.hdslb.com/sub.json?auth_key=opaque'}]}});return new Response(JSON.stringify({body:[{from:1,to:3,content:'第二P内容'}]}))});
 const result=await web.transcript('BV1xx411c7mD',2,'zh-CN');assert.equal(result.cid,222);assert.ok(paths.some(u=>u.includes('/wbi/v2')&&u.includes('cid=222')&&u.includes('aid=170001')&&u.includes('w_rid=')));assert.equal(result.modality,'subtitles');assert.deepEqual(result.coveredRanges,[[1,3]]);assert.equal(result.aiGenerated,true);assert.equal(result.binding?.subtitleId,'2064770642603168768');assert.ok(paths.some(u=>u.includes('auth_key=opaque')));assert.ok(!result.source?.includes('auth_key'));
 await assert.rejects(()=>new BiliWeb(async()=>new Response('',{status:412})).get('/x/web-interface/popular'),(e:any)=>e instanceof BiliError&&e.code==='risk_control');
 assert.equal(BilibiliConfigSchema.parse({}).enabled,false);
});

test('subtitle identity and signed endpoint cannot silently fall back to unrelated legacy data',async()=>{
 const paths:string[]=[];const web=new BiliWeb(async url=>{const u=String(url);paths.push(u);if(u.includes('/view'))return json({bvid:'BV1xx411c7mD',aid:170001,pages:[{page:1,cid:111,duration:10}]});if(u.includes('/nav'))return json({wbi_img:{img_url:'https://i0.hdslb.com/bfs/wbi/'+ 'a'.repeat(32)+'.png',sub_url:'https://i0.hdslb.com/bfs/wbi/'+ 'b'.repeat(32)+'.png'}});return json({aid:999,bvid:'BV1xx411c7mD',cid:111,subtitle:{subtitles:[{lan:'zh-CN',subtitle_url:'https://i0.hdslb.com/wrong.json'}]}})});
 await assert.rejects(()=>web.transcript('BV1xx411c7mD',1,'zh-CN'),(e:any)=>e.code==='subtitle_identity');assert.ok(!paths.some(u=>u.includes('wrong.json')));assert.ok(!paths.some(u=>u.includes('/x/player/v2')));
});

test('signed subtitles still reject out-of-range timelines and missing authenticated URLs',async()=>{
 for(const scenario of ['timeline','missing-url']){
  const web=new BiliWeb(async url=>{
   const u=String(url);
   if(u.includes('/view'))return json({bvid:'BV1xx411c7mD',aid:170001,pages:[{page:1,cid:111,duration:20}]});
   if(u.includes('/nav'))return json({wbi_img:{img_url:'https://i0.hdslb.com/bfs/wbi/'+ 'a'.repeat(32)+'.png',sub_url:'https://i0.hdslb.com/bfs/wbi/'+ 'b'.repeat(32)+'.png'}});
   if(u.includes('/wbi/v2'))return json({bvid:'BV1xx411c7mD',aid:170001,cid:111,subtitle:{subtitles:[{lan:'zh-CN',subtitle_url:scenario==='missing-url'?'':'https://i0.hdslb.com/sub.json'}]}});
   return new Response(JSON.stringify({body:[{from:1,to:999,content:'不属于本视频时间范围'}]}));
  });
  await assert.rejects(()=>web.transcript('BV1xx411c7mD',1,'zh-CN'),(error:any)=>error.code===(scenario==='timeline'?'subtitle_integrity':'subtitle_unavailable'));
 }
});


test('crash recovery preserves ambiguous receipt, reclaims only unstarted work and never replays writes',async t=>{
 const f=await fixture(t);
 const job=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'crash'}));await f.repo.claim('lost');
 await f.db.query("UPDATE tool_effects SET state='dispatching',lease_until=now()-interval '1 second' WHERE id=$1",[job.id]);
 assert.equal(await f.repo.claim('restarted'),null);
 const state=await f.repo.get(f.bot.tenant_id,f.id,job.id);assert.equal(state.state,'unknown');assert.equal(state.result.actionId,job.id);assert.equal(state.result.error.retry,'reconcile');
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'crash'},{workId:'another-work'})),/禁止再次提交/);
 const read=await f.repo.admit(f.bot,f.call('popular'));await f.repo.claim('lost-reader');
 await f.db.query("UPDATE tool_effects SET lease_until=now()-interval '1 second' WHERE id=$1",[read.id]);assert.equal((await f.repo.claim('replacement')).id,read.id);
});

test('account refresh saves new credentials before confirming old token, and recovery refuses unknown refresh',async t=>{
 const f=await fixture(t);let confirmed=false;
 const headers=new Headers();for(const v of ['SESSDATA=NEW; Path=/','bili_jct=NEW-CSRF; Path=/','DedeUserID=123; Path=/'])headers.append('set-cookie',v);
 const web=new BiliWeb(async(url)=>{
  const u=String(url);
  if(u.includes('/cookie/info'))return json({refresh:true,timestamp:Date.now()});
  if(u.includes('/correspond/'))return new Response('<div id="1-name">refresh-csrf</div>');
  if(u.includes('/cookie/refresh'))return json({refresh_token:'NEXT-REFRESH'},headers);
  if(u.includes('/nav'))return json({isLogin:true,mid:123,uname:'same'});
  if(u.includes('/confirm/refresh')){const [a]=await f.db.query('SELECT * FROM bilibili_accounts WHERE bot_id=$1',[f.id]);assert.equal(a.refresh_state,'confirm_pending');assert.equal(f.broker.open(f.bot.tenant_id,f.id,a.secret).refreshToken,'NEXT-REFRESH');assert.equal(a.revision,2);confirmed=true;return json({})}
  throw new Error('unexpected URL');
 });
 const broker=new BiliAccounts(f.db,f.key,web);await f.db.query("UPDATE bilibili_accounts SET refresh_state='requested' WHERE bot_id=$1",[f.id]);
 let [a]=await f.db.query('SELECT * FROM bilibili_accounts WHERE bot_id=$1',[f.id]);await broker.refresh(a,new AbortController().signal);assert.equal(confirmed,true);
 [a]=await f.db.query('SELECT * FROM bilibili_accounts WHERE bot_id=$1',[f.id]);assert.equal(a.refresh_state,'idle');assert.equal(a.pending_confirm,null);
 await f.db.query("UPDATE bilibili_accounts SET refresh_state='refreshing' WHERE bot_id=$1",[f.id]);await broker.recover();[a]=await f.db.query('SELECT * FROM bilibili_accounts WHERE bot_id=$1',[f.id]);assert.equal(a.refresh_state,'refresh_unknown');assert.equal(a.state,'auth_required');await assert.rejects(()=>broker.credential(f.bot.tenant_id,f.id),/账号未就绪/);
});

test('unsafe numeric platform ID stays unknown rather than confirming a rounded identifier',async t=>{
 const f=await fixture(t),executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async()=>json({rpid:9007199254740996})));
 const row=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'numeric'}));await executor.execute(await f.repo.claim('writer'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,row.id)).result.receipt.status,'unknown');
});


test('cancellation before admission response leaves a tombstone and prevents a late dispatch',async t=>{
 const f=await fixture(t),call=f.call('comment',{aid:'170001',text:'canceled-before-admit'});
 await f.repo.cancelWork(f.bot,call.workId,call.stepId);await assert.rejects(()=>f.repo.admit(f.bot,call),/工作已取消/);
 const other=f.call('popular');const job=await f.repo.admit(f.bot,other);await f.repo.cancelWork(f.bot,other.workId,other.stepId);assert.equal(await f.repo.claim('worker'),null);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,job.id)).state,'canceled');
});


test('durable media completion is leased once, carries original request and cannot cross tenant or revision',async t=>{
 const f=await fixture(t);await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:true,scopes:['public.read','media.analyze'],model:{provider:'bot'}});
 const job=await f.repo.admit(f.bot,f.call('analyze',{bvid:'BV1xx411c7mD',question:'看画面'},{origin:{scope:'private:768687099',eventId:'original',objective:'看画面'},deadlineAt:Date.now()+300000}));
 const claimed=await f.repo.claim('media');await f.repo.renew(claimed);
 await f.repo.finish(claimed,{schemaVersion:'fishbot.tool-result.v1',actionId:job.id,execution:'completed',receipt:{status:'not_applicable'},data:{modality:'frames',analysis:'真实测试结果'},usage:{modelRequests:1,costStatus:'unknown'},versions:{adapter:'test'}});
 const completion=await f.repo.completion(f.bot);assert.ok(completion);assert.equal(completion.id,job.id);assert.equal(completion.eventId,'original');assert.equal(completion.scope,'private:768687099');assert.equal(await f.repo.completion(f.bot),null);
 assert.equal(await f.repo.completion({...f.bot,tenant_id:'other'}),null);
 await f.repo.acknowledgeCompletion(f.bot,job.id,completion.owner,'done');assert.equal(await f.repo.completion(f.bot),null);
 const [row]=await f.db.query('SELECT completion_state FROM tool_effects WHERE id=$1',[job.id]);assert.equal(row.completion_state,'done');
});

test('media renew and completion both honor cancellation, offline state and changed settings',async t=>{
 const f=await fixture(t);await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:true,scopes:['media.analyze'],model:{provider:'bot'}});
 const job=await f.repo.admit(f.bot,f.call('analyze',{bvid:'BV1xx411c7mD',question:'看帧'},{origin:{scope:'private:768687099',eventId:'orig',objective:'看帧'}})),claimed=await f.repo.claim('media');
 await f.db.query('UPDATE tool_effects SET cancel_requested=true WHERE id=$1',[job.id]);await assert.rejects(()=>f.repo.renew(claimed),/工作或配置/);
 await f.repo.finish(claimed,{schemaVersion:'fishbot.tool-result.v1',actionId:job.id,execution:'canceled',receipt:{status:'rejected'},usage:{modelRequests:0,costStatus:'unknown'},versions:{adapter:'test'}});
 await f.db.query("UPDATE bots SET desired_state='offline' WHERE id=$1",[f.id]);assert.equal(await f.repo.completion({...f.bot,desired_state:'offline'}),null);
});


test('migration preserves existing enabled public search without granting private or write tools',async t=>{
 const f=await fixture(t);await f.db.query('DELETE FROM bilibili_settings WHERE bot_id=$1',[f.id]);await f.db.query("UPDATE bot_config_revisions SET config=jsonb_set(config,'{behavior,bilibiliSearchEnabled}','true') WHERE bot_id=$1",[f.id]);
 const sql=await readFile(path.resolve('packages/database/migrations/009_bilibili_tools.sql'),'utf8'),backfill=sql.slice(sql.lastIndexOf('INSERT INTO bilibili_settings'));await f.db.query(backfill);
 const settings=await f.repo.settings(f.bot.tenant_id,f.id);assert.equal(settings.config.enabled,true);assert.equal(settings.config.allowAutonomous,true);assert.deepEqual(settings.config.scopes,['public.read']);assert.equal(settings.config.model.provider,'none');
});

test('idempotent tool results cannot be inherited by another sender or stale configuration',async t=>{
 const f=await fixture(t),call=f.call('popular');await f.repo.admit(f.bot,call);
 await assert.rejects(()=>f.repo.admit(f.bot,{...call,senderId:'999999'}),/原始身份/);
 await assert.rejects(()=>f.repo.admit(f.bot,{...call,origin:{scope:'group:123456',eventId:'forged',objective:'same'}}),/原始身份/);
 await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:false});
 await assert.rejects(()=>f.repo.admit(f.bot,call),/工作或配置/);
});

test('coin spending requires verified identity, known sufficient balance and known previous spend',async t=>{
 const f=await fixture(t);let balance:any=0,previous:any=0,posts=0;
 const web=new BiliWeb(async(url,options)=>{const u=String(url);if(options?.method==='POST'){posts++;return json({})}if(u.includes('/nav'))return json({isLogin:true,mid:123,uname:'account',money:balance});if(u.includes('/coins'))return json({multiply:previous});throw new Error('unexpected endpoint')});
 const executor=new BilibiliExecutor(f.db,f.root,f.key,web);
 const attempt=async(step:string)=>{const row=await f.repo.admit(f.bot,f.call('coin',{bvid:'BV1xx411c7mD',count:1},{stepId:step}));await executor.execute(await f.repo.claim('coin-test'),new AbortController().signal);return(await f.repo.get(f.bot.tenant_id,f.id,row.id)).result};
 assert.equal((await attempt('empty')).error.code,'coin_balance_insufficient');assert.equal(posts,0);
 balance=undefined;assert.equal((await attempt('unknown')).error.code,'coin_balance_unknown');assert.equal(posts,0);
 balance=2;previous=undefined;assert.equal((await attempt('unknown-prior')).error.code,'coin_state_unknown');assert.equal(posts,0);
 previous=2;assert.equal((await attempt('full')).error.code,'coin_limit');assert.equal(posts,0);
 previous=0;assert.equal((await attempt('allowed')).receipt.status,'confirmed');assert.equal(posts,1);
});

test('blocked work during cooldown neither contacts Bilibili nor extends the cooldown',async t=>{
 const f=await fixture(t);let calls=0;const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async()=>{calls++;return json({})}));
 await f.db.query("INSERT INTO tool_service_cooldowns(service,until_at,reason) VALUES('bilibili',now()+interval '1 minute','original')");
 const [before]=await f.db.query("SELECT until_at,reason FROM tool_service_cooldowns WHERE service='bilibili'");
 const row=await f.repo.admit(f.bot,f.call('popular'));await executor.execute(await f.repo.claim('cooldown-test'),new AbortController().signal);
 const result=(await f.repo.get(f.bot.tenant_id,f.id,row.id)).result;assert.equal(result.error.code,'service_cooldown');assert.equal(calls,0);
 const [after]=await f.db.query("SELECT until_at,reason FROM tool_service_cooldowns WHERE service='bilibili'");assert.equal(+after.until_at,+before.until_at);assert.equal(after.reason,before.reason);
});

test('controlled video permissions reject other videos and mismatched AV/BV before a comment dispatch',async t=>{
 const f=await fixture(t);await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:true,scopes:['like.write','comment.write'],videoWriteAllowlist:['BV1xx411c7mD']});
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('like',{bvid:'BV1Nn436kEPz',value:true})),/许可范围/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'missing bvid'})),/许可范围/);
 let posts=0;const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(_url,options)=>{if(options?.method==='POST')posts++;return json({aid:170002})}));
 const row=await f.repo.admit(f.bot,f.call('comment',{aid:'170001',bvid:'BV1xx411c7mD',text:'different target'}));await executor.execute(await f.repo.claim('write-target'),new AbortController().signal);
 assert.equal(posts,0);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,row.id)).result.error.code,'target_identity_mismatch');
});

test('completed autonomous tasks cannot admit new tools; only their existing asynchronous media can finish',async t=>{
 const f=await fixture(t);await f.repo.configure(f.bot.tenant_id,f.id,2,{enabled:true,allowAutonomous:true,scopes:['public.read','media.analyze','comment.write'],model:{provider:'bot'}});
 await f.db.query("INSERT INTO autonomy_settings(bot_id,tenant_id,epoch,config) VALUES($1,$2,1,'{\"mode\":\"active\"}') ON CONFLICT(bot_id) DO UPDATE SET epoch=1,config='{\"mode\":\"active\"}'",[f.id,f.bot.tenant_id]);
 await f.db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES('bili-cycle',$1,$2,1,$3,$4,'test','executing','{}')",[f.id,f.bot.tenant_id,f.bot.generation,f.bot.revision]);
 await f.db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at) VALUES('bili-parent','bili-cycle',$1,$2,1,'research','','video','executing',now()+interval '5 minutes')",[f.id,f.bot.tenant_id]);
 const authority={authority:'autonomous',senderId:undefined,epoch:1,workId:'bili-parent'} as const;
 const job=await f.repo.admit(f.bot,f.call('analyze',{bvid:'BV1xx411c7mD',question:'看图'},authority)),claimed=await f.repo.claim('media');
 assert.equal(claimed.id,job.id);
 await f.db.query("UPDATE autonomy_actions SET status='completed',expires_at=now()-interval '1 second' WHERE id='bili-parent'");
 await f.repo.renew(claimed);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('comment',{aid:'170001',text:'new'},authority)),/自主工作授权/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('analyze',{bvid:'BV1xx411c7mD',question:'new'},{...authority,stepId:'new-media'})),/自主工作授权/);
 await f.db.query("UPDATE autonomy_settings SET epoch=2 WHERE bot_id=$1",[f.id]);await assert.rejects(()=>f.repo.renew(claimed),/自主工作授权/);
});


test('cooldown never blocks local durable results or opens credential/network access',async t=>{
 const f=await fixture(t);const s=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,scopes:[...s.config.scopes,'media.analyze']});
 let network=0;const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async()=>{network++;throw Error('network must remain unused')}));
 executor.accounts.credential=async()=>{throw Error('local result must not decrypt credentials')};
 await f.db.query("INSERT INTO tool_service_cooldowns(service,until_at,reason) VALUES('bilibili',now()+interval '1 minute','preserve')");
 const row=await f.repo.admit(f.bot,f.call('analysis_history',{}));await executor.execute(await f.repo.claim('local-results'),new AbortController().signal);
 const result=(await f.repo.get(f.bot.tenant_id,f.id,row.id)).result;assert.equal(result.execution,'completed');assert.deepEqual(result.data.analyses,[]);assert.equal(network,0);
 const own=await f.repo.admit(f.bot,f.call('task_result',{actionId:row.id},{stepId:'missing-media-result'}));await executor.execute(await f.repo.claim('local-result'),new AbortController().signal);
 assert.equal((await f.repo.get(f.bot.tenant_id,f.id,own.id)).result.error.code,'task_missing');assert.equal(network,0);
});

test('public reads use only the originally bound account and fail closed on a late rotation',async t=>{
 const f=await fixture(t);let requests=0;const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(_url,init)=>{requests++;assert.match(String(new Headers(init?.headers).get('cookie')),/SESSDATA=SESSION-SECRET/);return json({list:[]})}));
 const row=await f.repo.admit(f.bot,f.call('popular'));await executor.execute(await f.repo.claim('bound-public'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,row.id)).result.execution,'completed');assert.equal(requests,1);
 const next=await f.repo.admit(f.bot,f.call('popular',{}, {stepId:'rotated'})),load=executor.accounts.credential.bind(executor.accounts);executor.accounts.credential=async(...args)=>{const r=await load(...args);return{...r,record:{...r.record,revision:r.record.revision+1}}};
 await executor.execute(await f.repo.claim('late-rotation'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,next.id)).result.error.code,'account_changed');assert.equal(requests,1);
});


test('folder management and watch-later require separate grants; no existing permission expands',async t=>{
 const f=await fixture(t);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('folder_create',{title:'test'})),/未授权/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('watch_later',{bvid:'BV1xx411c7mD',value:true})),/未授权/);
 const s=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,scopes:[...s.config.scopes,'folder.manage','watch_later.write'],videoWriteAllowlist:['BV1xx411c7mD']});
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('watch_later',{bvid:'BV1xx411c7mE',value:true})),/许可范围/);
 assert.throws(()=>BilibiliConfigSchema.parse({...s.config,scopes:['not-a-scope']}));
});

test('single-folder ownership is checked before edit/delete and created ID must be real',async t=>{
 const f=await fixture(t),s=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,scopes:[...s.config.scopes,'folder.manage']});
 const posts:any[]=[];let invalidCreate=false;
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(url,init)=>{
  const u=new URL(String(url));if(init?.method==='POST'){posts.push({path:u.pathname,body:new URLSearchParams(String(init.body))});return json(u.pathname.endsWith('/add')?(invalidCreate?{}:{id:321}):{})}
  assert.equal(u.searchParams.get('up_mid'),'123');return json({list:[{id:321}]});
 }));
 const created=await f.repo.admit(f.bot,f.call('folder_create',{title:'test'}));await executor.execute(await f.repo.claim('create'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,created.id)).result.receipt.platformObjectId,'321');assert.equal(posts[0].body.get('privacy'),'1');
 const bad=await f.repo.admit(f.bot,f.call('folder_delete',{folderId:'999'}));await executor.execute(await f.repo.claim('wrong-owner'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,bad.id)).result.error.code,'folder_not_owned');assert.equal(posts.length,1);
 const edited=await f.repo.admit(f.bot,f.call('folder_edit',{folderId:'321',title:'new',intro:'retained description',private:false}));await executor.execute(await f.repo.claim('edit'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,edited.id)).result.receipt.status,'confirmed');assert.equal(posts[1].body.get('media_id'),'321');assert.equal(posts[1].body.get('intro'),'retained description');
 invalidCreate=true;const missing=await f.repo.admit(f.bot,f.call('folder_create',{title:'ambiguous'}, {stepId:'ambiguous-folder'}));await executor.execute(await f.repo.claim('missing-id'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,missing.id)).result.receipt.status,'unknown');
});

test('watch-later resolves the actual AV and sends only the requested single-video change',async t=>{
 const f=await fixture(t),s=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,scopes:[...s.config.scopes,'watch_later.write']});
 const sent:any[]=[];const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(url,init)=>{const u=new URL(String(url));if(init?.method==='POST'){sent.push({path:u.pathname,body:new URLSearchParams(String(init.body))});return json({})}assert.equal(u.pathname,'/x/web-interface/view');return json({aid:123456,bvid:'BV1xx411c7mD'})}));
 for(const value of [true,false]){const row=await f.repo.admit(f.bot,f.call('watch_later',{bvid:'BV1xx411c7mD',value},{stepId:String(value)}));await executor.execute(await f.repo.claim('watch-later'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,row.id)).result.receipt.status,'confirmed')}
 assert.deepEqual(sent.map(x=>x.path),['/x/v2/history/toview/add','/x/v2/history/toview/del']);for(const x of sent){assert.equal(x.body.get('aid'),'123456');assert.equal(x.body.get('viewed'),null);assert.equal(x.body.get('csrf'),'CSRF-SECRET')}
});


test('continuation grants remain bound to the original Work and live completion owner',async t=>{
 const f=await fixture(t),s=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,scopes:[...s.config.scopes,'media.analyze']});
 const origin={scope:'private:768687099',eventId:'actual-original-event',objective:'看后点赞'},parent=await f.repo.admit(f.bot,f.call('analyze',{bvid:'BV1xx411c7mD',question:'看画面'},{origin,deadlineAt:Date.now()+300000})),claimed=await f.repo.claim('parent');
 await f.repo.finish(claimed,{schemaVersion:'fishbot.tool-result.v1',actionId:parent.id,execution:'completed',receipt:{status:'not_applicable'},data:{modality:'frames'},usage:{modelRequests:0,costStatus:'unknown'},versions:{adapter:'fixture'}});
 const completion=await f.repo.completion(f.bot);assert.ok(completion);assert.equal(completion.workId,parent.work_id);const continuation={actionId:parent.id,owner:completion.owner};
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('like',{bvid:'BV1xx411c7mD',value:true},{origin,continuation:{...continuation,owner:'00000000-0000-4000-8000-000000000000'}})),/续接/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('like',{bvid:'BV1xx411c7mD',value:true},{origin,continuation,workId:'other-work'})),/续接/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('like',{bvid:'BV1xx411c7mD',value:true},{origin,continuation,senderId:'999'})),{statusCode:403,message:'恢复目标与真实发送者不符'});
 const child=await f.repo.admit(f.bot,f.call('like',{bvid:'BV1xx411c7mD',value:true},{origin,continuation}));assert.equal(child.continuation.sourceActionId,parent.id);
 await f.repo.acknowledgeCompletion(f.bot,parent.id,completion.owner,'done');
 await assert.rejects(()=>f.repo.validate(child),/续接/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('comment',{aid:'123',text:'hello'},{origin,continuation})),/续接/);
 await f.repo.cancelWork(f.bot,child.work_id,child.step_id);
 const failed=await f.repo.admit(f.bot,f.call('analyze',{bvid:'BV1xx411c7mD',question:'另一个分析问题'},{origin,workId:'failed-read-original-work'})),failedClaim=await f.repo.claim('failed-media');
 assert.equal(failedClaim.id,failed.id);
 await f.repo.finish(failedClaim,{schemaVersion:'fishbot.tool-result.v1',actionId:failed.id,execution:'failed',receipt:{status:'rejected'},error:{code:'vision_response_invalid',retry:'never',userMessage:'图片模型为空'},usage:{modelRequests:1,costStatus:'unknown'},versions:{adapter:'fixture'}});
 const recovery=await f.repo.completion(f.bot);assert.ok(recovery);assert.equal(recovery.id,failed.id);
 const read=await f.repo.admit(f.bot,f.call('analysis_history',{}, {origin,workId:failed.work_id,continuation:{actionId:failed.id,owner:recovery.owner}}));
 await f.repo.validate(read);
 const write=await f.repo.admit(f.bot,f.call('like',{bvid:'BV1xx411c7mD',value:false},{origin,workId:failed.work_id,continuation:{actionId:failed.id,owner:recovery.owner}}));await f.repo.validate(write);
 const currentSettings=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,currentSettings.revision,{...currentSettings.config,enabled:false});
 await assert.rejects(()=>f.repo.validate(write),/失效/);
});

test('Guard waiting media remains queued without blocking light reads and can still be canceled',async t=>{
 const f=await fixture(t),s=await f.repo.settings(f.bot.tenant_id,f.id);
 await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,scopes:[...s.config.scopes,'media.analyze']});
 const media=await f.repo.admit(f.bot,f.call('analyze',{bvid:'BV1xx411c7mD',question:'看画面'}));
 const light=await f.repo.admit(f.bot,f.call('popular',{}, {workId:'light-read'}));let calls=0;
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async()=>{calls++;return json({list:[]})}));
 executor.lastBridge=Date.now();executor.mediaReady=false;
 await executor.run(new AbortController().signal);
 assert.equal((await f.repo.get(f.bot.tenant_id,f.id,media.id)).state,'queued');
 assert.equal((await f.repo.get(f.bot.tenant_id,f.id,light.id)).state,'finished');
 assert.equal(calls,1);assert.equal(executor.mediaBusy,undefined);
 await f.repo.cancelWork(f.bot,media.work_id,media.step_id);
 await executor.run(new AbortController().signal);
 assert.equal((await f.repo.get(f.bot.tenant_id,f.id,media.id)).state,'canceled');
 assert.equal(calls,1);
});

test('private list cursors stop at returned rows, paginate local lists and do not mark notifications without a grant',async t=>{
 const f=await fixture(t),requests:URL[]=[];
 const web=new BiliWeb(async url=>{const u=new URL(String(url));requests.push(u);
  if(u.pathname.endsWith('/history/cursor'))return json({cursor:{max:999,view_at:999,business:'archive'},list:[{title:'first',view_at:300,history:{oid:10,bvid:'BV1xx411c7mD',business:'archive'}},{title:'second',view_at:200,history:{oid:20,business:'archive'}}]});
  if(u.pathname.endsWith('/toview'))return json({list:[{id:1,title:'first'},{id:2,title:'second'},{id:3,title:'third'}]});
  if(u.pathname.endsWith('/feed/all'))return json({has_more:true,offset:'999',items:[{id_str:'100',modules:{}},{id_str:'90',modules:{}}]});
  if(u.pathname.endsWith('/msgfeed/reply'))return json({cursor:{is_end:false,id:999,time:999},items:[{id:10,reply_time:300},{id:20,reply_time:200}]});
  throw Error('unexpected endpoint '+u.pathname);
 });
 const executor=new BilibiliExecutor(f.db,f.root,f.key,web),signal=new AbortController().signal;
 const run=async(operation:any,args:any,stepId:string)=>{const j=await f.repo.admit(f.bot,f.call(operation,args,{stepId}));await executor.execute(await f.repo.claim('pagination'),signal);return(await f.repo.get(f.bot.tenant_id,f.id,j.id)).result};
 const first=await run('library',{kind:'history',limit:1},'history-1');assert.deepEqual(first.data.nextCursor,{max:'10',viewAt:300,business:'archive'});
 await run('library',{kind:'history',limit:1,cursor:first.data.nextCursor},'history-2');assert.equal(requests.at(-1)?.searchParams.get('max'),'10');assert.equal(requests.at(-1)?.searchParams.get('view_at'),'300');
 const later=await run('library',{kind:'watch_later',page:2,limit:1},'later-2');assert.equal(later.data.items[0].title,'second');assert.equal(later.data.nextPage,3);
 const feed=await run('feed',{kind:'following',limit:1,offset:'120'},'feed-1');assert.equal(feed.data.nextOffset,'100');assert.equal(requests.at(-1)?.searchParams.get('offset'),'120');
 const notifications=await run('notification_items',{kind:'replies',limit:1,cursor:{id:'50',time:400}},'notifications-1');assert.deepEqual(notifications.data.nextCursor,{id:'10',time:300});assert.equal(requests.at(-1)?.searchParams.get('reply_time'),'400');
 const settings=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,settings.revision,{...settings.config,scopes:settings.config.scopes.filter((s:string)=>s!=='notification.ack')});
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('notification_items',{kind:'replies'},{stepId:'no-notify-grant'})),/未授权/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('library',{kind:'history',page:2},{stepId:'bad-page'})),/cursor/);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('dm_read',{beforeSeq:'123'},{stepId:'bad-dm'})),/真实对话/);
});

test('UP discovery and dynamic detail stay bounded and use real target IDs with signed public requests',async t=>{
 const f=await fixture(t),requests:URL[]=[];let posts=0;
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(url,options)=>{const u=new URL(String(url));requests.push(u);if(options?.method==='POST')posts++;
  if(u.pathname.endsWith('/nav'))return json({wbi_img:{img_url:'https://i0.hdslb.com/bfs/wbi/0123456789abcdef0123456789abcdef.png',sub_url:'https://i0.hdslb.com/bfs/wbi/fedcba9876543210fedcba9876543210.png'}});
  if(u.pathname.endsWith('/acc/info'))return json({mid:456,name:'UP profile',sign:'public bio',level:5});
  if(u.pathname.endsWith('/arc/search'))return json({page:{count:6},list:{vlist:[{bvid:'BV1xx411c7mD',title:'public video'}]}});
  if(u.pathname.endsWith('/archive/tags'))return json([{tag_id:1,tag_name:'标签'}]);
  if(u.pathname.endsWith('/v1/detail'))return json({item:{id_str:'900001',modules:{module_author:{mid:456,name:'UP profile'},module_dynamic:{desc:{text:'external public text'},major:{draw:{items:Array.from({length:20},()=>({src:'https://i0.hdslb.com/pic.jpg',width:500,height:400}))}}}}}});
  throw Error('unexpected endpoint');
 }));
 const run=async(operation:any,args:any)=>{const j=await f.repo.admit(f.bot,f.call(operation,args));await executor.execute(await f.repo.claim('discovery'),new AbortController().signal);return(await f.repo.get(f.bot.tenant_id,f.id,j.id)).result};
 const profile=await run('user_profile',{uid:'456'});assert.equal(profile.data.uid,'456');assert.equal(profile.data.name,'UP profile');assert.ok(requests.at(-1)?.searchParams.has('w_rid'));assert.equal(requests.at(-1)?.searchParams.get('mid'),'456');
 const videos=await run('user_videos',{uid:'456',page:1,limit:2,order:'click'});assert.equal(videos.data.nextPage,2);assert.equal(requests.at(-1)?.searchParams.get('order'),'click');
 const tags=await run('video_tags',{bvid:'BV1xx411c7mD'});assert.deepEqual(tags.data.tags,[{id:'1',name:'标签'}]);
 const dynamic=await run('dynamic_info',{dynamicId:'900001'});assert.equal(dynamic.data.images.length,9);assert.equal(dynamic.data.text,'external public text');assert.equal(dynamic.data.author.uid,'456');assert.equal(posts,0);assert.ok(!JSON.stringify(dynamic).includes('SESSION-SECRET'));
 const settings=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,settings.revision,{...settings.config,scopes:['public.read']});
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('dynamic_info',{dynamicId:'900001'},{stepId:'without-private-grant'})),/未授权/);
});

test('collection discovery, separate pagination and contents use bounded read-only tools with revocable permission',async t=>{
 const f=await fixture(t),requests:URL[]=[];
 await f.db.query('DELETE FROM bilibili_accounts WHERE bot_id=$1',[f.id]); // Anonymous reads require explicit permission.
 const initial=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,initial.revision,{...initial.config,allowAnonymous:true});
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(url,options)=>{
  assert.equal(options?.method,'GET');assert.equal((options?.headers as any)?.Cookie,undefined);
  const u=new URL(String(url));requests.push(u);
  if(u.pathname.endsWith('seasons_series_list'))return json({items_lists:{seasons_list:[{meta:{season_id:42,mid:456,name:'合集',total:3}}],series_list:[{meta:{series_id:43,mid:456,name:'系列',total:2}}]}});
  if(u.pathname.endsWith('seasons_archives_list'))return json({meta:{season_id:42,mid:456,name:'合集'},page:{total:3},archives:[{aid:1,bvid:'BV1xx411c7mD',title:'第一集'}]});
  if(u.pathname.endsWith('/series/archives'))return json({page:{total:2},archives:[{aid:2,bvid:'BV1Nn436kEPz',title:'第二集'}]});
  if(u.pathname.endsWith('/view'))return json({aid:1,bvid:'BV1xx411c7mD',title:'视频',owner:{mid:456},pages:[],ugc_season:{id:42,mid:456,title:'合集'}});
  throw Error('unexpected endpoint');
 }));
 const run=async(operation:any,args:any,step=operation)=>{const j=await f.repo.admit(f.bot,f.call(operation,args,{stepId:step}));await executor.execute(await f.repo.claim('collections'),new AbortController().signal);return(await f.repo.get(f.bot.tenant_id,f.id,j.id)).result};
 const discovery=await run('collections',{uid:'456',limit:1});assert.equal(discovery.data.seasons[0].collectionId,'42');assert.equal(discovery.data.series[0].collectionId,'43');assert.equal(discovery.data.nextPage,2);
 const season=await run('collection_videos',{uid:'456',collectionId:'42',kind:'season',page:2,limit:1});assert.equal(season.data.nextPage,3);assert.equal(requests.at(-1)?.searchParams.get('season_id'),'42');assert.equal(requests.at(-1)?.searchParams.get('page_num'),'2');
 const series=await run('collection_videos',{uid:'456',collectionId:'43',kind:'series',page:2,limit:1},'series');assert.equal(series.data.nextPage,null);assert.equal(requests.at(-1)?.searchParams.get('series_id'),'43');assert.equal(requests.at(-1)?.searchParams.get('pn'),'2');
 const video=await run('video_info',{bvid:'BV1xx411c7mD'});assert.deepEqual(video.data.collection,{kind:'season',collectionId:'42',uid:'456',title:'合集'});
 const stale=await f.repo.admit(f.bot,f.call('collections',{uid:'456'},{stepId:'queued-before-disable'}));
 const s=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,collectionsEnabled:false});
 await assert.rejects(()=>f.repo.validate(stale));await assert.rejects(()=>f.repo.admit(f.bot,f.call('collections',{uid:'456'},{stepId:'disabled'})),/未授权/);
 const fresh=await f.repo.settings(f.bot.tenant_id,f.id);assert.ok(!executor.catalog({config:fresh.config}).some(o=>o.operation==='collections'||o.operation==='collection_videos'));
 assert.ok(executor.catalog({config:fresh.config}).some(o=>o.operation==='video_info'));
 const without=await run('video_info',{bvid:'BV1xx411c7mD'},'video-without-collection');assert.equal(without.data.collection,null);
 const other=await f.app.inject({method:'GET',url:'/v1/bots/'+f.id+'/bilibili',cookies:f.other});assert.equal(other.statusCode,404);
});

test('video link resolver follows only bounded Bilibili redirects without credentials or arbitrary URLs',async()=>{
 const requested:string[]=[];
 const web=new BiliWeb(async(url,options)=>{requested.push(String(url));assert.equal(options?.redirect,'manual');assert.equal((options?.headers as any)?.Cookie,undefined);return new Response(null,{status:302,headers:{location:'https://www.bilibili.com/video/BV1Nn436kEPz/?spm_id_from=share'}})});
 const result=await web.resolveVideo('https://b23.tv/Test123',new AbortController().signal);assert.equal(result.bvid,'BV1Nn436kEPz');assert.equal(requested.length,1);
 await assert.rejects(()=>web.resolveVideo('https://127.0.0.1/video/BV1Nn436kEPz',new AbortController().signal),/不访问其他地址/);assert.equal(requested.length,1);
 const bad=new BiliWeb(async()=>new Response(null,{status:302,headers:{location:'http://169.254.169.254/latest/meta-data/'}}));await assert.rejects(()=>bad.resolveVideo('https://b23.tv/Test123',new AbortController().signal),/地址不允许/);
 const schema=(await import('../packages/contracts/bilibili.js')).biliOperations.resolve_video.args;
 assert.throws(()=>schema.parse({url:'https://www.bilibili.com.evil.test/video/BV1Nn436kEPz'}));assert.throws(()=>schema.parse({url:'https://b23.tv:444/Test123'}));
});

test('platform access requires login or explicit anonymous permission and never silently drops account identity',async t=>{
 assert.equal(BilibiliConfigSchema.parse({}).allowAnonymous,false);
 const f=await fixture(t);let requests=0;
 const executor=new BilibiliExecutor(f.db,f.root,f.key,new BiliWeb(async(_url,options)=>{requests++;assert.match((options?.headers as any).Cookie,/SESSDATA=SESSION-SECRET/);return json({aid:1,bvid:'BV1xx411c7mD',owner:{mid:456},pages:[]})}));
 const authenticated=await f.repo.admit(f.bot,f.call('video_info',{bvid:'BV1xx411c7mD'},{stepId:'authenticated'}));
 await executor.execute(await f.repo.claim('account-policy'),new AbortController().signal);assert.equal((await f.repo.get(f.bot.tenant_id,f.id,authenticated.id)).result.execution,'completed');assert.equal(requests,1);
 const s=await f.repo.settings(f.bot.tenant_id,f.id);await f.repo.configure(f.bot.tenant_id,f.id,s.revision,{...s.config,allowAnonymous:true});
 const stale=await f.repo.admit(f.bot,f.call('video_info',{bvid:'BV1xx411c7mD'},{stepId:'stale-account'}));
 await f.db.query("UPDATE bilibili_accounts SET state='auth_required' WHERE bot_id=$1",[f.id]);
 await assert.rejects(()=>f.repo.validate(stale),/账号未就绪/);
 await executor.execute(await f.repo.claim('no-anonymous-fallback'),new AbortController().signal);assert.equal(requests,1);
 const config=(await f.repo.settings(f.bot.tenant_id,f.id)).config;
 assert.ok(executor.catalog({config,account_state:'auth_required'}).some(o=>o.operation==='video_info'),'configured declarations survive account expiry; validate/execute above still deny access');
 await f.db.query("UPDATE bilibili_accounts SET state='ready' WHERE bot_id=$1",[f.id]);
 const unlink=await f.app.inject({method:'DELETE',url:'/v1/bots/'+f.id+'/bilibili/account',cookies:f.cookies,payload:{expectedRevision:1}});assert.equal(unlink.statusCode,200,unlink.body);
 const off=await f.repo.settings(f.bot.tenant_id,f.id);assert.equal(off.config.enabled,false);assert.equal(off.config.allowAnonymous,false);
 await f.repo.configure(f.bot.tenant_id,f.id,off.revision,{...off.config,enabled:true});
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('video_info',{bvid:'BV1xx411c7mD'},{stepId:'unbound-default'})),/手动开启匿名访问/);
 const guarded=await f.repo.settings(f.bot.tenant_id,f.id);assert.ok(executor.catalog({config:guarded.config}).some(o=>o.operation==='video_info'),'declared capability is not anonymous execution permission');
 await f.repo.configure(f.bot.tenant_id,f.id,guarded.revision,{...guarded.config,allowAnonymous:true});
 const anonymous=await f.repo.admit(f.bot,f.call('video_info',{bvid:'BV1xx411c7mD'},{stepId:'explicit-anonymous'}));assert.equal(anonymous.account_revision,null);
 await assert.rejects(()=>f.repo.admit(f.bot,f.call('account_info',{},{stepId:'anonymous-private'})),/未授权|未绑定|登录/);
 // A new binding cannot change the identity of an already queued anonymous Work.
 await f.db.query("INSERT INTO bilibili_accounts(bot_id,tenant_id,uid,label,secret) VALUES($1,$2,'123','test account',$3)",[f.id,f.bot.tenant_id,f.broker.seal(f.bot.tenant_id,f.id,f.credential)]);
 await assert.rejects(()=>f.repo.validate(anonymous),/账号已变化/);
});


test('autonomous QQ and Bili reserve one shared cycle budget before dispatch; late unknown is irrevocable',async t=>{
 const f=await fixture(t),cycle='budget-cycle',work='budget-work';
 await f.db.query("UPDATE bilibili_settings SET config=jsonb_set(config,'{allowAutonomous}','true') WHERE bot_id=$1",[f.id]);
 await f.db.query("INSERT INTO autonomy_settings(bot_id,tenant_id,epoch,config) VALUES($1,$2,1,'{\"mode\":\"active\"}')",[f.id,f.bot.tenant_id]);
 await f.db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES($1,$2,$3,1,1,1,'fixture','acting','{\"cycleExternalActions\":0,\"day\":\"fixture\"}')",[cycle,f.id,f.bot.tenant_id]);
 await f.db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at) VALUES($1,$2,$3,$4,1,'research','','budget','executing',now()+interval '1 hour')",[work,cycle,f.id,f.bot.tenant_id]);
 await f.db.query('UPDATE autonomy_cycles SET generation=$2,config_revision=$3 WHERE id=$1',[cycle,f.bot.generation,f.bot.revision]);
 const qq=(logical:string,phase:any='reserve')=>qqExternalBudget(f.db,f.bot,{workId:work,epoch:1,logicalActionId:logical,phase});
 const row=await f.repo.admit(f.bot,f.call('like',{bvid:'BV1xx411c7mD',value:true},{workId:work,authority:'autonomous',senderId:undefined,epoch:1}));
 const job=await f.repo.claim('budget-owner');
 await assert.rejects(()=>qq('qq-0'),/external_action_budget/);
 await assert.rejects(()=>f.repo.beginDispatch(job,new AbortController().signal),/external_action_budget/);
 assert.equal((await f.repo.get(f.bot.tenant_id,f.id,row.id)).state,'claimed');
 assert.equal(await externalCount(f.db,cycle),0);
 await f.db.query("UPDATE autonomy_cycles SET budget=jsonb_set(budget,'{cycleExternalActions}','1') WHERE id=$1",[cycle]);
 // Controlled transaction barriers: abort or expire the lease after the
 // cycle reservation check, before beginDispatch returns a dispatch permit.
 for(const fault of ['abort','expire'] as const){
  const controller=new AbortController();
  const guarded=new ToolEffects({...f.db,transaction:fn=>f.db.transaction(tx=>fn({...tx,query:async(sql,params)=>{
   if(fault==='expire'&&sql.startsWith("UPDATE tool_effects SET state='dispatching'"))await tx.query("UPDATE tool_effects SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1",[job.id]);
   const rows=await tx.query(sql,params);
   if(fault==='abort'&&sql.includes('AS total'))controller.abort();
   return rows;
  }}))});
  assert.equal(await guarded.beginDispatch(job,controller.signal),false);
  assert.equal(await externalCount(f.db,cycle),0,'no reservation for a proven unstarted dispatch');
  await f.db.query("UPDATE tool_effects SET lease_until=now()+interval '1 hour' WHERE id=$1",[job.id]);
 }
 const race=await Promise.allSettled([qq('qq-1'),f.repo.beginDispatch(job,new AbortController().signal)]);
 assert.equal(race.filter(r=>r.status==='fulfilled').length,1,'one physical dispatch can win QQ+Bili concurrency');
 assert.equal(await externalCount(f.db,cycle),1);
 if(race[0].status==='fulfilled'){
  await qq('qq-1');assert.equal(await externalCount(f.db,cycle),1,'same logical output is billed once');
  await qq('qq-1','released');assert.equal(await externalCount(f.db,cycle),0,'proven pre-dispatch stop releases');
  assert.equal(await f.repo.beginDispatch(job,new AbortController().signal),true);
 }
 await f.repo.finish(job,{schemaVersion:'fishbot.tool-result.v1',actionId:row.id,execution:'failed',receipt:{status:'unknown'},usage:{modelRequests:0,costStatus:'unknown'},versions:{adapter:'fixture'}});
 await assert.rejects(()=>qq('after-unknown'),/external_action_budget/);
 assert.equal((await biliWorkEffects(f.db,f.id,f.bot.tenant_id,work))[0].status,'unknown');
 await f.db.query("UPDATE autonomy_cycles SET finished_at=now(),usage='{\"externalActions\":1}' WHERE id=$1",[cycle]);
 await assert.rejects(()=>qq('late-new'),/已失效/);
 await f.db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,external_actions) VALUES($1,$2,'fixture',1)",[f.id,f.bot.tenant_id]);
 await f.repo.finish(job,{schemaVersion:'fishbot.tool-result.v1',actionId:row.id,execution:'completed',receipt:{status:'confirmed'},usage:{modelRequests:0,costStatus:'actual'},versions:{adapter:'fixture'}});
 assert.equal((await biliWorkEffects(f.db,f.id,f.bot.tenant_id,work))[0].status,'confirmed','late original owner can settle unknown without dispatch');
 assert.equal((await f.db.query("SELECT external_actions FROM autonomy_daily_usage WHERE bot_id=$1 AND day='fixture'",[f.id]))[0].external_actions,1);
 assert.equal(await externalCount(f.db,cycle),1);
});

test('QQ unknown cannot be refunded or billed twice across replay and late receipt',async t=>{
 const f=await fixture(t),cycle='qq-cycle',work='qq-work';
 await f.db.query("INSERT INTO autonomy_settings(bot_id,tenant_id,epoch,config) VALUES($1,$2,1,'{\"mode\":\"active\"}')",[f.id,f.bot.tenant_id]);
 await f.db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES($1,$2,$3,1,1,1,'fixture','acting','{\"cycleExternalActions\":1,\"day\":\"fixture\"}')",[cycle,f.id,f.bot.tenant_id]);
 await f.db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at) VALUES($1,$2,$3,$4,1,'research','','budget','executing',now()+interval '1 hour')",[work,cycle,f.id,f.bot.tenant_id]);
 await f.db.query('UPDATE autonomy_cycles SET generation=$2,config_revision=$3 WHERE id=$1',[cycle,f.bot.generation,f.bot.revision]);
 const update=(phase:any)=>qqExternalBudget(f.db,f.bot,{workId:work,epoch:1,logicalActionId:'same',phase});
 await update('reserve');await update('unknown');await update('released');await update('reserve');
 assert.equal(await externalCount(f.db,cycle),1);
 await f.db.query("UPDATE autonomy_cycles SET finished_at=now(),usage='{\"externalActions\":1}' WHERE id=$1",[cycle]);
 assert.equal((await update('confirmed')).status,'confirmed');
 assert.equal(await externalCount(f.db,cycle),1);
});


test('anonymous WBI navigation accepts only actual logged-out signing metadata',async()=>{
 const images={img_url:'https://i0.hdslb.com/bfs/wbi/0123456789abcdef0123456789abcdef.png',sub_url:'https://i0.hdslb.com/bfs/wbi/fedcba9876543210fedcba9876543210.png'};
 let requested=0;
 const web=new BiliWeb(async(url)=>{requested++;return new Response(JSON.stringify(String(url).includes('/nav')?{code:-101,data:{isLogin:false,wbi_img:images}}:{code:0,data:{verified:true}}),{headers:{'Content-Type':'application/json'}})});
 assert.deepEqual(await web.signed('/x/player/wbi/v2',{aid:'1',cid:'2'}),{verified:true});assert.equal(requested,2);
 // No sign-in exception for expired account identity, malformed signing material,
 // other platform errors, or another endpoint carrying the same data.
 for(const [code,data] of [[-101,{isLogin:false,wbi_img:{...images,img_url:'https://evil.test/a.png'}}],[-101,{isLogin:false}],[-352,{isLogin:false,wbi_img:images}],[-101,{isLogin:true,wbi_img:images}]] as const){
  const rejected=new BiliWeb(async()=>new Response(JSON.stringify({code,data})));
  await assert.rejects(()=>rejected.signed('/x/player/wbi/v2',{aid:'1'}));
 }
 const expired=new BiliWeb(async()=>new Response(JSON.stringify({code:-101,data:{isLogin:false,wbi_img:images}})));
 await assert.rejects(()=>expired.signed('/x/player/wbi/v2',{aid:'1'},{cookies:{SESSDATA:'expired'},refreshToken:''}));
 await assert.rejects(()=>expired.get('/x/web-interface/view'),(error:any)=>error.code==='auth_required');
});
