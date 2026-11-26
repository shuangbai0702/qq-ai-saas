/** In-process shared Worker release boundary. Run from the trusted local
 * deployment operator; never expose apply/rollback as an HTTP endpoint.
 * Disk installation stays with the release owner. QQ and Journal are borrowed.
 */
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
import {prepareAiStop} from './prepare-ai-stop.mjs';

const marker=Symbol.for('fishbot.worker-update');
function transientReadiness(error){
 for(let value=error,depth=0;value&&depth<5;value=value.cause,depth++)
  if(['ECONNREFUSED','ECONNRESET','EPIPE','ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_SOCKET'].includes(value.code)||value.name==='TimeoutError')return true;
 return /HTTP (408|429|50[0234])\b/.test(String(error?.message));
}

export async function updateSharedWorkers(agent,{release,heartbeat,apply,rollback,verify,signal,timeoutMs=120000,onProgress=()=>{},afterStop=async()=>{}}){
 assert.match(release,/^[a-f0-9]{40,64}$/);
 assert.ok(Number.isFinite(timeoutMs)&&timeoutMs>0&&timeoutMs<=120000);
 for(const fn of [heartbeat,apply,rollback,verify,onProgress,afterStop])assert.equal(typeof fn,'function');
 assert.ok(!agent.closed,'Agent is closing');
 assert.ok(!agent[marker],'Another Worker update owns this Agent');
 for(const key of Object.getOwnPropertySymbols(agent))
  if(key.description?.startsWith('fishbot.release.'))assert.ok(!agent[key]?.held,'Another release owns this Agent');
 // Acquire before the first await. A disconnected caller cannot release a
 // second operation's ownership, nor start another late installation.
 const owner={release,phase:'draining'};agent[marker]=owner;
 const tick=Object.getOwnPropertyDescriptor(agent,'tick');
 const autonomy=agent.autonomy,autonomyTick=Object.getOwnPropertyDescriptor(autonomy,'tick');
 const bili=agent.bilibili,favorites=agent.favorites;
 const biliStopped=bili.stopped,favoritesClosed=favorites.closed;
 const protocols=new Map(),groups=new Map(),affected=new Set();
 let attempted=false,pulse,pulsing=false,lastPulse=Promise.resolve(),pulseError,failure;
 const report=async(phase,detail={})=>{owner.phase=phase;await onProgress({release,phase,...detail})};
 const check=(recovering=false)=>{
  assert.equal(agent[marker],owner,'Worker update ownership changed');
  assert.ok(!agent.closed,'Agent is closing');if(!recovering)signal?.throwIfAborted();
  if(pulseError&&!recovering)throw pulseError;
 };
 const wait=async(predicate,label,recovering=false)=>{
  const deadline=Date.now()+timeoutMs;let lastReport=0;
  for(;;){check(recovering);const done=await predicate();check(recovering);
   if(Date.now()>=deadline)throw Error('Worker update timed out: '+label);
   if(done)return;
   if(Date.now()-lastReport>=5000){lastReport=Date.now();await report(owner.phase,{waitingFor:label,deadline})}
   await delay(Math.min(50,Math.max(1,deadline-Date.now())),undefined,{signal:recovering?undefined:signal});
  }
 };
 const sameProtocols=async()=>{
  for(const [id,old]of protocols){
   const now=await agent.host.record(id,'protocol');
   assert.ok(now&&now.pid===old.pid&&now.created===old.created,'QQ owner changed');
   assert.ok(await agent.host.inspect(id,'protocol'),'QQ owner is unavailable');
  }
 };
 const currentGroup=async(old)=>{
  const [now]=await agent.db.query('SELECT * FROM runtime_groups WHERE id=$1',[old.id]);
  assert.ok(now&&now.generation===old.generation&&now.desired_state==='running','Worker generation changed');
  return now;
 };
 const members=async(group)=>agent.db.query("SELECT * FROM bots WHERE group_id=$1 AND desired_state='running' AND deleted_at IS NULL ORDER BY id",[group.id]);
 const ready=async(group)=>{
  const current=await currentGroup(group),bots=await members(current);
  let health;
  try{health=await agent.shared.request(current,'/health')}
  catch(error){if(transientReadiness(error))return false;throw error}
  if(health.ready!==true||health.quiescing||Object.keys(health.memberErrors||{}).length)return false;
  assert.equal(health.generation,current.generation,'Worker generation changed');
  for(const bot of bots){const member=health.members?.[bot.id];
   if(member?.generation!==bot.generation||member?.revision!==bot.revision)return false;
  }
  groups.get(current.id).health=health;
  return true;
 };
 const start=async(entry)=>{
  let current=await currentGroup(entry.group),first=true;
  for(const bot of await members(current)){
   await sameProtocols();let failure;
   try{await agent.shared.start(await agent.context(bot))}catch(error){failure=error}
   const [now]=await agent.db.query('SELECT * FROM runtime_groups WHERE id=$1',[current.id]);
   // The original start owner advances a stopped group once. Track that
   // authorized transition even if its response was lost; later members must
   // attach to the same generation. Other transitions remain fatal.
   assert.ok(now&&now.desired_state==='running'&&(now.generation===current.generation||first&&now.generation===current.generation+1),'Worker generation changed');
   entry.group={...now};current=entry.group;first=false;
   if(failure)throw failure;
  }
 };
 const restore=(target,name,descriptor)=>{if(descriptor)Object.defineProperty(target,name,descriptor);else delete target[name]};
 try{
  // Pause admission, not existing external operations. Keep autonomous Work
  // heartbeats renewing while it naturally settles; never cancel user Work.
  agent.tick=async()=>{};autonomy.tick=async()=>{};bili.stopped=true;favorites.closed=true;
  await report('draining');
  pulse=setInterval(()=>{
   if(pulsing)return;pulsing=true;
   lastPulse=Promise.resolve().then(()=>heartbeat({maintenance:release})).catch(error=>{pulseError=error}).finally(()=>{pulsing=false});
  },500);pulse.unref();
  await wait(()=>agent.controlIdle()&&!autonomy.busy&&!autonomy.active&&!autonomy.heartbeatTask,'Agent control and autonomous Work');
  const running=await agent.db.query("SELECT * FROM bots WHERE desired_state='running' AND deleted_at IS NULL ORDER BY id");check();
  assert.ok(running.length&&running.every(bot=>bot.runtime_mode==='shared'&&bot.group_id),'Expected shared Worker members');
  for(const bot of running){const record=await agent.host.record(bot.id,'protocol');assert.ok(record);protocols.set(bot.id,record)}
  await sameProtocols();
  for(const id of new Set(running.map(bot=>bot.group_id))){
   const [row]=await agent.db.query('SELECT * FROM runtime_groups WHERE id=$1',[id]);check();
   assert.ok(row&&row.desired_state==='running');const group={...row},expected=await agent.host.record(id,'ai');assert.ok(expected);
   const entry={group,expected,lease:undefined};groups.set(id,entry);
   await wait(async()=>{
    const current=await currentGroup(group),health=await agent.shared.request(current,'/health');check();
    assert.equal(health.stopFence,true,'Worker has no atomic stop fence');
    if(!health.activity?.idle)return false;
    try{entry.lease=await prepareAiStop(agent,current,expected);return true}
    catch(error){if(/HTTP 409/.test(String(error)))return false;throw error}
   },'Worker idle boundary');
  }
  await sameProtocols();check();await report('installing');attempted=true;
  await apply();check();
  for(const [id,entry]of groups){
   await sameProtocols();check();const current=await currentGroup(entry.group);
   const now=await agent.host.record(id,'ai');assert.deepEqual([now?.pid,now?.created],[entry.expected.pid,entry.expected.created],'AI owner changed');
   const health=await agent.shared.request(current,'/health');check();
   assert.ok(health.quiescing===true&&health.activity?.idle===true,'Worker lost its stop boundary');
   // Record before stopping: a lost stop response still requires recovery.
   affected.add(id);await report('reloading',{groupId:id});
   await agent.host.stop(id,'ai','worker-update',{pid:entry.expected.pid,created:entry.expected.created});
   await afterStop({group:current,previous:entry.expected});check();
   await start(entry);await wait(()=>ready(entry.group),'new Worker readiness');
  }
  check();await sameProtocols();await report('verifying');
  const proof=await verify({groups:[...groups.values()].map(({group,health})=>({group,health}))});check();
  assert.ok(proof?.release===release&&proof.runtimeApplied===true&&proof.journalDigestsUnchanged===true,'Missing runtime and Journal verification');
  for(const {group}of groups.values())await wait(()=>ready(group),'final Worker readiness');
  await sameProtocols();check();await report('applied');
  return{release,pid:process.pid,qqPreserved:protocols.size,workers:groups.size,proof};
 }catch(error){
  owner.phase='recovering';const errors=[error];failure=error;
  try{await report('recovering',{errorType:error?.name||'Error'})}catch(error){errors.push(error)}
  // A replacement may already execute the rejected release. Settle/fence and
  // stop its exact owner before changing disk code, not after rollback.
  let restored=true;
  if(attempted){
   for(const id of affected){const entry=groups.get(id);let record;
    try{
     await wait(async()=>{
      const current=await currentGroup(entry.group);record=await agent.host.record(id,'ai');
      if(!record||!await agent.host.inspect(id,'ai')){record=undefined;return true}
      let health;try{health=await agent.shared.request(current,'/health')}catch(error){if(transientReadiness(error))return false;throw error}
      if(health.activity?.idle!==true)return false;
      try{entry.recoveryLease=await prepareAiStop(agent,current,record);return true}
      catch(error){if(/HTTP 409/.test(String(error)))return false;throw error}
     },'rollback Worker idle boundary',true);
     if(record)try{await agent.host.stop(id,'ai','worker-update-rollback',{pid:record.pid,created:record.created})}
     catch(error){if(await agent.host.inspect(id,'ai'))throw error}
    }catch(failure){errors.push(failure);restored=false}
   }
   if(restored)try{await rollback()}catch(failure){errors.push(failure);restored=false}
  }
  for(const [id,entry]of groups)try{
   if(affected.has(id)&&restored&&!agent.closed){
    await start(entry);
    await wait(()=>ready(entry.group),'rollback Worker readiness',true);
   }
  }catch(failure){errors.push(failure)}
  if(errors.length>1){failure=new AggregateError(errors,'Worker update failed; recovery requires inspection');throw failure}
  throw error;
 }finally{
  const errors=[];
  for(const entry of groups.values())for(const lease of [entry.lease,entry.recoveryLease])try{await lease?.cancel()}catch(error){errors.push(error)}
  clearInterval(pulse);await lastPulse;
  // Restore descriptors even when cancellation/verification failed. Old
  // model calls and external tasks keep their original owner and receipts.
  restore(agent,'tick',tick);restore(autonomy,'tick',autonomyTick);
  bili.stopped=biliStopped;favorites.closed=favoritesClosed;
  if(agent[marker]===owner)delete agent[marker];
  if(errors.length)throw new AggregateError([...(failure?[failure]:[]),...errors],'Worker update admission could not be reopened');
 }
}
