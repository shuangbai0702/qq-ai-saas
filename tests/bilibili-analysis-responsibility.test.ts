import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';
import {openToolWork} from './helpers/tool-work.js';

async function fixture(t:any){
 const root=await mkdtemp(path.resolve('.local/analysis-responsibility-')),db=await openDatabase(root);
 await db.query("INSERT INTO tenants(id,name) VALUES('tenant','tenant')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('bot','tenant','fixture','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}})]);
 await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES('bot',1,'{}')");
 const [bot]=await db.query("SELECT * FROM bots WHERE id='bot'");
 const native=await openToolWork(root,db,bot,'first-work',1),repo=native.repo;
 t.after(async()=>{await native.close();await db.close();await rm(root,{recursive:true,force:true})});
 await repo.configure('tenant','bot',1,{enabled:true,allowAutonomous:true,scopes:['media.analyze'],model:{provider:'openai',baseUrl:'https://fixture.invalid/v1',model:'fixture'}},'fixture-only');
 await db.query("INSERT INTO bilibili_accounts(bot_id,tenant_id,uid,label,secret) VALUES('bot','tenant','123','fixture','not-a-real-credential')");
 await db.query("INSERT INTO autonomy_settings(bot_id,tenant_id,epoch,config,lease_owner,lease_until) VALUES('bot','tenant',1,'{\"mode\":\"active\"}','fixture',now()+interval '5 minutes')");
 await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,source) VALUES('goal','bot','tenant','model','active','fixture','fixture','{\"scope\":\"self\"}')");
 for(const [id,epoch] of [['first-work',1],['next-work',1],['next-epoch-work',2]]){
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,work_expires_at) VALUES($1,'bot','tenant',$2,$3,$4,'fixture','acting','{}',now()+interval '5 minutes')",[id,epoch,bot.generation,bot.revision]);
  if(id!=='first-work')await native.sql("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,work_id,authority_epoch,expires_at,execution_started) VALUES('self','service',?,?,'autonomous','model_tools','received','',?,?,?,0)",[id,Date.now()/1000,id,epoch,Date.now()/1000+300]);
 }
 const call=(stepId:string,patch:any={})=>({operation:'analyze' as const,args:{bvid:'BV1xx411c7mD',question:'inspect the same detail'},workId:'first-work',stepId,authority:'autonomous' as const,epoch:1,deadlineAt:Date.now()+90000,origin:{scope:'group:11111111',eventId:'fixture',objective:'fixture'},...patch});
 return{db,repo,bot,call,native};
}

test('exact pending analysis shares its original Work responsibility while independent Work, epoch and account stay distinct',async t=>{
 const {db,repo,bot,call,native}=await fixture(t);
 const first=await repo.admit(bot,call('first'));
 await native.sql("UPDATE pipeline_events SET status='unknown' WHERE source='tool' AND event=?",[first.id]);
 const sameGoal=await repo.admit(bot,call('same-work'));
 assert.equal(sameGoal.id,first.id);assert.equal(sameGoal.work_id,'first-work');assert.equal(sameGoal.state,'unknown');
 const otherScope=await repo.admit(bot,call('independent-work',{workId:'next-work'}));
 assert.notEqual(otherScope.id,first.id);
 await db.query("UPDATE autonomy_settings SET epoch=2 WHERE bot_id='bot'");
 const nextEpoch=await repo.admit(bot,call('next-epoch',{workId:'next-epoch-work',epoch:2}));
 assert.notEqual(nextEpoch.id,first.id);assert.equal(nextEpoch.mode_epoch,2);
 // Deliberately keep the revision equal to exercise the separate UID fence.
 await db.query("UPDATE bilibili_accounts SET uid='456' WHERE bot_id='bot'");
 const nextAccount=await repo.admit(bot,call('next-account',{workId:'next-epoch-work',epoch:2}));
 assert.notEqual(nextAccount.id,nextEpoch.id);assert.equal(nextAccount.account_uid,'456');
 assert.equal((await native.sql("SELECT count(*) AS n FROM pipeline_events WHERE source='tool'"))[0].n,4);
});

test('analysis responsibility deduplication preserves changed question and requested range as new observations',async t=>{
 const {repo,bot,call}=await fixture(t),original=await repo.admit(bot,call('original'));
 const changedQuestion=await repo.admit(bot,call('question',{workId:'next-work',args:{...call('unused').args,question:'inspect a new detail'}}));
 const changedRange=await repo.admit(bot,call('range',{workId:'next-work',args:{...call('unused').args,start:30,seconds:15}}));
 assert.notEqual(changedQuestion.id,original.id);assert.notEqual(changedRange.id,original.id);assert.notEqual(changedRange.id,changedQuestion.id);
 assert.equal(changedRange.args.start,30);assert.equal(changedRange.args.seconds,15);
});
