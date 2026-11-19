import assert from 'node:assert/strict';
import {openDatabase} from '../../packages/database/index.js';
import {AutonomyCoordinator} from '../../apps/node-agent/src/autonomous.js';

const [root,phase]=process.argv.slice(2),db=await openDatabase(root);let calls=0;
const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{
 if(op==='snapshot')return{dshBusy:phase==='queue',proactiveEnabled:true};
 if(op==='policy')return{};
 if(op==='tool')return{pending:[],effects:[]};
 assert.equal(op,'execute');calls++;
 return{status:'completed',continuationAccepted:true,effects:[],usage:{tokens:10,modelCalls:1,toolCalls:0}};
});
try{
 if(phase==='queue'){
  await db.query("INSERT INTO tenants(id,name) VALUES('t','t')");
  await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('b','t','b','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
  await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) SELECT id,revision,'{\"proactive\":true}' FROM bots");
  await coordinator.repo.configure('t','b',1,{mode:'active',activityEnabled:false});
  for(const id of ['first','first','second'])await coordinator.repo.wake('t','b',2,'operator-wake',0,id);
  await coordinator.tick();await coordinator.active?.task;assert.equal(calls,0);
  assert.deepEqual((await db.query("SELECT scope FROM autonomy_wakes WHERE reason='operator-wake' ORDER BY created_at,dedupe_key")).map(r=>r.scope),['first','second']);
 }else{
  await db.query('UPDATE bots SET health=$1',[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
  for(let i=0;i<2;i++){
   await db.query("UPDATE autonomy_wakes SET due_at=now()-interval '1 second'");await coordinator.tick();await coordinator.active?.task;
  }
  assert.equal(calls,2);
  assert.deepEqual((await db.query("SELECT decision->>'activationWakeConsumed' consumed FROM autonomy_cycles WHERE decision->>'activationWakeConsumed' IS NOT NULL ORDER BY started_at")).map(r=>r.consumed),['operator-wake:first','operator-wake:second']);
  await coordinator.repo.wake('t','b',2,'operator-wake',0,'first');await coordinator.tick();await coordinator.active?.task;assert.equal(calls,2);
 }
}finally{await coordinator.stop();await db.close()}
console.log(JSON.stringify({phase,calls,pid:process.pid}));
