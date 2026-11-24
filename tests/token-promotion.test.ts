import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createApp} from '../apps/control-api/src/app.js';
import {loadKey,encrypt} from '../packages/shared/security.js';
import {platformSecretPurpose} from '../apps/control-api/src/platform-model.js';
import {creditBalance,reserveCredits,settleCredits} from '../apps/control-api/src/platform-credits.js';
import {tokenPromotion} from '../packages/shared/promotion.js';
import {platformModelCredential} from '../packages/shared/platform-model.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {issueTestInvite} from './helpers/invitations.js';

test('manual promotion is tenant-bound, once-only, expiring and settles late reservations without reviving expired credit',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-promotion-')),key=await loadKey(root);
 await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,JSON.stringify({primary:'fixture-primary',fallback:'fixture-fallback'})));
 const calls:{url:string;body:any}[]=[];let failure=503;
 const {app,db}=await createApp({root,databaseUrl:'',agent:false,platformModelTransport:async(url,init)=>{
  const body=JSON.parse(String(init?.body));calls.push({url:String(url),body});
  if(String(url).startsWith('https://crab-ai.net/')){if(failure===0)throw Error('unknown request outcome');return Response.json({error:{message:'unavailable'}},{status:failure})}
  return Response.json({choices:[{message:{role:'assistant',content:'fixture'}}],usage:{prompt_tokens:10,completion_tokens:2,total_tokens:12}});
 }});
 try{
  async function member(email:string){
   const inviteCode=await issueTestInvite(db),result=await app.inject({method:'POST',url:'/v1/auth/register',payload:{email,name:'fixture',password:'fixture-password',inviteCode}});assert.equal(result.statusCode,200,result.body);
   const cookie=result.cookies.map(c=>c.name+'='+c.value).join(';'),[row]=await db.query('SELECT tenant_id FROM memberships WHERE user_id=$1',[result.json().user.id]);return {cookie,tenant:row.tenant_id};
  }
  const first=await member('first@promotion.test'),other=await member('other@promotion.test');
  const get=(cookie=first.cookie)=>app.inject({method:'GET',url:'/v1/activities',headers:{cookie}});
  const claim=(cookie=first.cookie)=>app.inject({method:'POST',url:'/v1/activities/claim',headers:{cookie},payload:{tenantId:other.tenant}});
  assert.equal((await app.inject({url:'/v1/activities'})).statusCode,401);
  const originalEndsAt=tokenPromotion.endsAt;
  try{
   tokenPromotion.endsAt=new Date(Date.now()+86400000).toISOString();
   const initial=(await get()).json();assert.equal(initial.id,tokenPromotion.id);assert.equal(initial.claimOpen,true);assert.equal(initial.balance.available,0);
   const results=await Promise.all([claim(),claim(),claim()]);for(const result of results)assert.equal(result.statusCode,200,result.body);
   const claimed=(await get()).json();assert.equal(claimed.balance.granted,5_000_000);assert.equal(claimed.balance.available,5_000_000);
   assert.equal(new Date(claimed.balance.campaign.expiresAt).getTime()-new Date(claimed.balance.campaign.claimedAt).getTime(),7*86400000);
   assert.equal((await db.query("SELECT * FROM audit_events WHERE event='activity.tokens_claimed'")).length,1);
   assert.equal((await get(other.cookie)).json().balance.granted,0);
   tokenPromotion.endsAt='2000-01-01T00:00:00.000Z';assert.equal((await claim(other.cookie)).statusCode,409);assert.equal((await claim()).json().alreadyClaimed,true);
  }finally{tokenPromotion.endsAt=originalEndsAt}
  assert.equal((await creditBalance(db,other.tenant)).granted,0);
  const created=await app.inject({method:'POST',url:'/v1/bots',headers:{cookie:first.cookie},payload:{name:'controlled fixture',protocol:'mock',config:{...defaultConfig,platformModel:true}}});assert.equal(created.statusCode,201,created.body);
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[created.json().id]);
  // Existing invitation credits remain permanent, with expiring credits spent first.
  await db.query('UPDATE platform_model_credits SET granted=granted+10000 WHERE tenant_id=$1',[first.tenant]);
  const payload={model:'platform',max_tokens:32,messages:[{role:'system',content:'stable'},{role:'user',content:'[QQ语音] 合成测试'}],prompt_cache_key:'fixture'};
  const call=()=>app.inject({method:'POST',url:'/v1/platform-model/chat/completions',headers:{authorization:'Bearer '+platformModelCredential(key,bot)},payload});
  assert.equal((await call()).statusCode,200);assert.equal(calls.length,2);assert.deepEqual(calls[0].body.messages,payload.messages);assert.deepEqual(calls[1].body.messages,payload.messages);assert.equal(calls[1].body.prompt_cache_key,'fixture');
  let balance=await creditBalance(db,first.tenant);assert.equal(balance.campaign.spent,12);assert.equal(balance.reserved,0);
  for(const status of [401,403,404,429]){failure=status;const before:number=calls.length;assert.equal((await call()).statusCode,200);assert.equal(calls.length,before+2)}
  failure=0;const before:number=calls.length;assert.equal((await call()).statusCode,502);assert.equal(calls.length,before+1);
  // Release the explicitly retained unknown reservation only in this isolated fixture.
  for(const row of await db.query('SELECT id FROM platform_model_reservations'))await settleCredits(db,row.id,0);
  const reserve=(tokens:number)=>db.transaction(tx=>reserveCredits(tx,first.tenant,bot.id,tokens));
  const held=await reserve(1000),late=await reserve(2000);
  await db.query("UPDATE platform_model_credits SET campaign_expires_at=clock_timestamp()-interval '1 second' WHERE tenant_id=$1",[first.tenant]);
  balance=await creditBalance(db,first.tenant);assert.equal(balance.available,10000);assert.equal(balance.campaign.available,0);
  await settleCredits(db,held,100);await settleCredits(db,held,100);await settleCredits(db,late,0);
  balance=await creditBalance(db,first.tenant);assert.equal(balance.available,10000);assert.equal(balance.reserved,0);assert.equal(balance.campaign.spent,160);
  assert.equal((await claim()).json().alreadyClaimed,true);assert.equal((await creditBalance(db,first.tenant)).available,10000);
  await assert.rejects(db.transaction(async tx=>{await reserveCredits(tx,first.tenant,bot.id,9000);throw Error('rollback')}));
  assert.equal((await creditBalance(db,first.tenant)).reserved,0);
  const concurrent=await Promise.allSettled([reserve(9000),reserve(9000)]);assert.equal(concurrent.filter(r=>r.status==='fulfilled').length,1);
  for(const r of concurrent)if(r.status==='fulfilled')await settleCredits(db,r.value,10);
  assert.equal((await creditBalance(db,first.tenant)).available,9990);assert.equal((await creditBalance(db,other.tenant)).available,0);
 }finally{await app.close();await rm(root,{recursive:true,force:true})}
});
