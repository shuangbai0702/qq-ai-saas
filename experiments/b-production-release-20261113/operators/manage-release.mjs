import assert from'node:assert/strict';import fs from'node:fs/promises';import{pathToFileURL}from'node:url';
import{execFile}from'node:child_process';import{promisify}from'node:util';
const dir='C:/FishBotPredeploy20261112/control/b-release-20261113';
const cfg=JSON.parse((await fs.readFile('C:/FishBotPredeploy20261112/mobile-private-config.json','utf8')).replace(/^\uFEFF/,''));
let baseline=JSON.parse(await fs.readFile(dir+'/baseline.json','utf8').catch(e=>{if(e.code==='ENOENT')return 'null';throw e}));
const key=Symbol.for('fishbot.release.b-20261113'),opKey=Symbol.for('fishbot.release.operation');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function protocolOwners(a){for(const p of baseline.processes){const r=await a.host.record(p.bot,'protocol');assert.equal(r.pid,p.protocol.pid);assert.equal(r.created,p.protocol.created);assert.equal(a.host.children.get(p.bot+':protocol')?.pid,r.pid);assert.equal(a.host.children.get(p.bot+':protocol')?.exitCode,null);assert.ok(await a.host.inspect(p.bot,'protocol'))}}
async function botsCurrent(a){for(const old of baseline.running){const [b]=await a.db.query('SELECT * FROM bots WHERE id=$1',[old.id]);assert.ok(b&&!b.deleted_at&&b.desired_state==='running');for(const k of ['generation','revision','group_id','runtime_mode'])assert.equal(b[k],old[k]);}}
async function groupHealth(a,id){const [g]=await a.db.query('SELECT * FROM runtime_groups WHERE id=$1',[id]);return[g,await a.shared.request(g,'/health')]}
async function ready(a){await protocolOwners(a);await botsCurrent(a);for(const b of baseline.running){const [live]=await a.db.query('SELECT * FROM bots WHERE id=$1',[b.id]);assert.equal((await a.shared.probe(await a.context(live))).ready,true)}}
async function workerIdentity(a,id,owner,pid){
 const record=await a.host.record(id,'ai');assert.equal(record.pid,owner.pid);assert.equal(record.created,owner.created);assert.ok(await a.host.inspect(id,'ai'));
 const code="import psutil,sys,json,os;p=psutil.Process(int(sys.argv[1]));assert p.create_time()==float(sys.argv[2]);c=psutil.Process(int(sys.argv[3]));assert c.pid==p.pid or c.pid in [x.pid for x in p.children(recursive=True)];assert os.path.normcase(os.path.abspath(sys.argv[4])) in [os.path.normcase(os.path.abspath(x)) for x in c.cmdline()];print(json.dumps({'pid':c.pid,'created':c.create_time(),'parent':p.pid}))";
 const r=await promisify(execFile)(cfg.environment.ASTRBOT_PYTHON,['-B','-c',code,String(owner.pid),String(owner.created),String(pid),cfg.sourceRoot+'/adapters/astrbot/shared-worker.py'],{timeout:10000});return JSON.parse(r.stdout);
}
async function atomicFence(a,g,owner,h){
 if(h.pid===owner.pid){const{prepareAiStop}=await import(pathToFileURL(cfg.sourceRoot+'/scripts/prepare-ai-stop.mjs'));return prepareAiStop(a,g,owner)}
 // Windows venv python.exe is a launcher. Verify both identities instead of
 // pretending the protocol's actual worker PID equals its ProcessHost owner.
 const child=await workerIdentity(a,g.id,owner,h.pid);let attempted=true;
 const cancel=async()=>{if(!attempted)return;const now=await workerIdentity(a,g.id,owner,h.pid);assert.equal(now.created,child.created);await a.shared.request(g,'/cancel-stop','POST',{});attempted=false};
 try{const r=await a.shared.request(g,'/prepare-stop','POST',{});assert.equal(r.pid,child.pid);assert.equal(r.quiescing,true);const now=await workerIdentity(a,g.id,owner,h.pid);assert.equal(now.created,child.created);return{cancel}}catch(e){await cancel();throw e}
}
async function fence(a,s,id){const deadline=Date.now()+120000;let g,h;while(Date.now()<deadline){[g,h]=await groupHealth(a,id);if(h.stopFence===true&&h.activity?.idle===true)break;await sleep(250)}assert.ok(h?.stopFence===true&&h.activity?.idle===true,'Worker did not drain');const old=await a.host.record(id,'ai');const lease=await atomicFence(a,g,old,h);s.fences.set(id,{old,lease});return old}
async function reload(a,s){for(const oldGroup of baseline.ais){const id=oldGroup.id;const old=await fence(a,s,id);await a.host.stop(id,'ai','b-increment-release',{pid:old.pid,created:old.created});s.fences.delete(id);s.stopped.add(id);const bots=await a.db.query("SELECT * FROM bots WHERE group_id=$1 AND desired_state='running' AND deleted_at IS NULL ORDER BY id",[id]);for(const b of bots){await protocolOwners(a);await a.shared.start(await a.context(b))}const n=await a.host.record(id,'ai');assert.notEqual(n.pid,old.pid);s.newPids.set(id,n);s.stopped.delete(id)}await ready(a)}
export async function manage(a,op){if(op==='baseline')return captureBaseline(a);assert.ok(baseline,'Capture a fresh baseline first');assert.equal(process.pid,baseline.agent.pid);assert.equal(a.closed,false);assert.equal(a[opKey],undefined,'Concurrent maintenance');a[opKey]='b-20261113:'+op;try{return await owned(a,op)}finally{delete a[opKey]}}
async function owned(a,op){
 if(op==='status'){await ready(a);const bots=await a.db.query('SELECT id,desired_state,observed_state,generation,revision,applied_revision,group_id,runtime_mode FROM bots WHERE deleted_at IS NULL ORDER BY id');const groups=[];for(const g of baseline.ais){const [row,h]=await groupHealth(a,g.id);const owner=await a.host.record(g.id,'ai');const identity=await workerIdentity(a,g.id,owner,h.pid);groups.push({id:g.id,generation:row.generation,health:{ready:h.ready,pid:h.pid,generation:h.generation,members:h.members,memberErrors:h.memberErrors,activity:h.activity,stopFence:h.stopFence,quiescing:h.quiescing},process:owner,identity})}return{pid:process.pid,held:!!a[key]?.held,stage:a[key]?.stage||'live',database:(await a.db.query('SELECT 1 AS ok'))[0],bots,groups,protocolOwnersPreserved:true,controlIdle:a.controlIdle?.(),autonomyClosed:a.autonomy.closed,bilibiliStopped:a.bilibili.stopped,favoritesClosed:a.favorites.closed}}
 if(op==='inspect'){await protocolOwners(a);await botsCurrent(a);return{pid:process.pid,held:!!a[key]?.held,stage:a[key]?.stage||'live'}}
 if(op==='hold'){
  assert.ok(Date.now()-Date.parse(baseline.capturedAt)<15*60*1000,'Baseline older than 15 minutes; recapture before hold');await protocolOwners(a);await botsCurrent(a);assert.ok(baseline.running.every(b=>b.runtime_mode==='shared'&&b.group_id),'Unknown dedicated owner');assert.ok(!a[key]?.held);
  for(const sym of Object.getOwnPropertySymbols(a))if(sym!==key&&sym.description?.startsWith('fishbot.release.'))assert.notEqual(a[sym]?.held,true,'Another release owns maintenance');
  const tickDescriptor=Object.getOwnPropertyDescriptor(a,'tick');const s=a[key]={held:true,stage:'holding',tickDescriptor,tick:a.tick,agentTimer:a.timer,autonomyClosed:a.autonomy.closed,bilibiliStopped:a.bilibili.stopped,favoritesClosed:a.favorites.closed,fences:new Map(),stopped:new Set(),newPids:new Map()};
  const heartbeat=JSON.parse(await fs.readFile(a.root+'/agent-heartbeat.json','utf8'));const{HeartbeatWriter}=await import(pathToFileURL(cfg.sourceRoot+'/dist-server/apps/node-agent/src/agent.js'));s.heartbeat=new HeartbeatWriter(a.root+'/agent-heartbeat.json',{pid:process.pid,processStart:heartbeat.processStart,startedAt:heartbeat.startedAt});
  a.tick=async()=>{};s.heartbeatTimer=setInterval(()=>void s.heartbeat.write({maintenance:'b-20261113'}).catch(()=>{}),500);s.heartbeatTimer.unref();
  const deadline=Date.now()+120000;while((!(a.controlIdle?.()??!a.busy)||a.operations.size||a.healthTask||a.autonomy.busy||a.bilibili.busy||a.bilibili.mediaBusy)&&Date.now()<deadline)await sleep(100);
  assert.ok((a.controlIdle?.()??!a.busy)&&!a.operations.size&&!a.healthTask,'Agent control did not drain');
  for(const r of await Promise.allSettled([a.autonomy.stop(),a.bilibili.stop(),a.favorites.stop()]))if(r.status==='rejected')throw r.reason;
  await ready(a);for(const g of baseline.ais)await fence(a,s,g.id);s.stage='fenced';return{pid:process.pid,held:true,atomicFences:s.fences.size,protocolOwners:baseline.processes.length};
 }
 const s=a[key];assert.ok(s?.held,'No held release');
 if(op==='backup-ready'){
  assert.equal(s.stage,'fenced');await protocolOwners(a);await botsCurrent(a);for(const id of s.fences.keys()){const[g,h]=await groupHealth(a,id);assert.ok(h.stopFence===true&&h.activity?.idle===true)}
  const {randomUUID}=await import('node:crypto');const permit={pid:process.pid,fenced:true,at:Date.now()/1000,token:randomUUID(),baselineCapturedAt:baseline.capturedAt};s.backupPermit=permit.token;await fs.writeFile(dir+'/backup-permit.json',JSON.stringify(permit));return permit;
 }
 if(op==='hotmaintenance'){
  assert.equal(s.stage,'fenced');
  const proto=Object.getPrototypeOf(a),markers=['fishbot.foreground.context','fishbot.autonomous.maintenance','fishbot.bilibili.maintenance','fishbot.favorite.maintenance'];
  s.runtimePrevious={proto,health:proto.health,context:proto.context,start:a.host.start,apply:a.ai.applyConfig,attach:a.shared.attach,autonomy:a.autonomy,bilibili:a.bilibili,favorites:a.favorites,markers:markers.map(name=>[name,Object.getOwnPropertyDescriptor(a,Symbol.for(name))])};
  const {createHash}=await import('node:crypto');const bytes=await fs.readFile(cfg.sourceRoot+'/dist-server/apps/node-agent/src/maintenance.js'),digest=createHash('sha256').update(bytes).digest('hex');
  const module=await import(pathToFileURL(cfg.sourceRoot+'/dist-server/apps/node-agent/src/maintenance.js').href+'?release='+digest);
  // Keep the original Agent interval alive, calling only the held no-op tick.
  // Installers must not clear/recreate this existing owner in their finally.
  const timer=a.timer;a.timer=undefined;
  try{
   const autonomy=await module.installAutonomousRuntime(a,digest,{start:false});assert.equal(a.autonomy.closed,true);assert.equal(a.autonomy.timer,undefined);
   const bilibili=await module.installBilibiliRuntime(a,digest);a.bilibili.stopped=true;
   const favorites=await module.installFavoriteRuntime(a,digest);a.favorites.closed=true;
   assert.notEqual(a.autonomy,s.runtimePrevious.autonomy);assert.notEqual(a.bilibili,s.runtimePrevious.bilibili);assert.notEqual(a.favorites,s.runtimePrevious.favorites);
   await protocolOwners(a);await botsCurrent(a);s.runtimeDigest=digest;
   return{pid:process.pid,release:digest,autonomy,bilibili,favorites,allOwnersHeld:true,agentTimerPreserved:true};
  }finally{a.timer=timer}
 }
 if(op==='reload'){
  assert.equal(s.stage,'fenced');assert.ok(s.runtimeDigest,'All maintenance owners must refresh before worker reload');s.stage='reloading';
  // Fences were obtained before replacing source. Stop the exact old owner,
  // then let the existing ProcessHost/SharedAdapter rejoin checked members.
  for(const oldGroup of baseline.ais){const id=oldGroup.id,entry=s.fences.get(id);assert.ok(entry);const current=await a.host.record(id,'ai');assert.equal(current.pid,entry.old.pid);assert.equal(current.created,entry.old.created);await protocolOwners(a);await botsCurrent(a);await a.host.stop(id,'ai','b-increment-release',{pid:current.pid,created:current.created});s.fences.delete(id);s.stopped.add(id);for(const b of await a.db.query("SELECT * FROM bots WHERE group_id=$1 AND desired_state='running' AND deleted_at IS NULL ORDER BY id",[id]))await a.shared.start(await a.context(b));const n=await a.host.record(id,'ai');assert.notEqual(n.pid,current.pid);s.newPids.set(id,n);s.stopped.delete(id)}
  await ready(a);s.stage='reloaded';return{pid:process.pid,ready:baseline.running.length,ais:[...s.newPids].map(([id,p])=>({id,pid:p.pid,created:p.created})),protocolOwnersPreserved:true};
 }
 if(op==='verify'){assert.equal(s.stage,'reloaded');await ready(a);s.stage='verified';return{pid:process.pid,ready:baseline.running.length,protocolOwnersPreserved:true}}
 if(op==='rollback'){
  if(s.runtimePrevious){
   const v=s.runtimePrevious;
   // Drain replacement owners before restoring references. Resuming the old
   // coordinator happens only after restored Python workers are ready.
   for(const r of await Promise.allSettled([a.autonomy.stop(),a.bilibili.stop(),a.favorites.stop()]))if(r.status==='rejected')throw r.reason;
   a.autonomy=v.autonomy;a.bilibili=v.bilibili;a.favorites=v.favorites;
   v.proto.health=v.health;v.proto.context=v.context;a.host.start=v.start;a.ai.applyConfig=v.apply;a.shared.attach=v.attach;
   for(const [name,descriptor] of v.markers){const k=Symbol.for(name);if(descriptor)Object.defineProperty(a,k,descriptor);else delete a[k]}
  }

  // Source restoration is performed under the same held release by the
  // driver, with exact before/after hashes checked before each replacement.
  for(const entry of s.fences.values())await entry.lease.cancel();s.fences.clear();
  for(const id of s.stopped){assert.equal(await a.host.inspect(id,'ai'),null);for(const b of await a.db.query("SELECT * FROM bots WHERE group_id=$1 AND desired_state='running' AND deleted_at IS NULL ORDER BY id",[id]))await a.shared.start(await a.context(b));s.stopped.delete(id)}
  if(s.newPids.size)await reload(a,s);await ready(a);s.stage='verified';return{pid:process.pid,rolledBack:true,protocolOwnersPreserved:true};
 }
 if(op==='resume'){
  assert.ok(['verified','fenced'].includes(s.stage));await ready(a);for(const entry of s.fences.values())await entry.lease.cancel();s.fences.clear();assert.equal(a.timer,s.agentTimer,'Agent timer owner changed');clearInterval(s.heartbeatTimer);await s.heartbeat.stop();if(s.tickDescriptor)Object.defineProperty(a,'tick',s.tickDescriptor);else delete a.tick;a.bilibili.stopped=s.bilibiliStopped;a.favorites.closed=s.favoritesClosed;a.autonomy.closed=s.autonomyClosed;if(!a.autonomy.closed)a.autonomy.start();s.held=false;s.stage='resumed';return{pid:process.pid,resumed:true,ready:baseline.running.length,protocolOwnersPreserved:true};
 }
 throw Error('Unsupported operation');
}

async function captureBaseline(a){
 assert.equal(a.closed,false);assert.equal(a[opKey],undefined);assert.ok(!a[key]?.held,'Cannot recapture held baseline');
 const running=await a.db.query("SELECT id,generation,revision,group_id,runtime_mode FROM bots WHERE desired_state='running' AND deleted_at IS NULL ORDER BY id");
 assert.ok(running.length>0&&running.every(b=>b.runtime_mode==='shared'&&b.group_id),'Only understood shared runtime owners may deploy');
 const services={};for(const name of ['agent','control-api','postgres'])services[name]=JSON.parse((await fs.readFile(cfg.stateRoot+'/services/'+name+'.process.json','utf8')).replace(/^\uFEFF/,''));
 assert.equal(services.agent.pid,process.pid);
 const processes=[];for(const b of running){const protocol=await a.host.record(b.id,'protocol');assert.equal(a.host.children.get(b.id+':protocol')?.pid,protocol.pid);assert.ok(await a.host.inspect(b.id,'protocol'));processes.push({bot:b.id,protocol,owned:true})}
 const ais=[];for(const id of [...new Set(running.map(b=>b.group_id))]){const [g]=await a.db.query('SELECT * FROM runtime_groups WHERE id=$1',[id]),h=await a.shared.request(g,'/health'),owner=await a.host.record(id,'ai');await workerIdentity(a,id,owner,h.pid);ais.push({id,...owner,workerPid:h.pid,owned:true})}
 const {createHash}=await import('node:crypto'),python={};for(const name of await fs.readdir(cfg.sourceRoot+'/adapters/astrbot'))if(name.endsWith('.py'))python[name]=createHash('sha256').update((await fs.readFile(cfg.sourceRoot+'/adapters/astrbot/'+name,'utf8')).replaceAll('\r\n','\n')).digest('hex');
 const backupBots=await a.db.query('SELECT id,protocol FROM bots WHERE deleted_at IS NULL ORDER BY id');const capturedAt=new Date().toISOString();baseline={capturedAt,agent:services.agent,running,processes,ais,backupBots};
 await fs.writeFile(dir+'/baseline.json',JSON.stringify(baseline));await fs.writeFile(dir+'/service-identities.json',JSON.stringify(services));
 const mediaBytes=await fs.readFile(cfg.dataRoot+'/media-resources.json').catch(e=>{if(e.code==='ENOENT')return null;throw e});const mediaBefore=mediaBytes===null?null:createHash('sha256').update(mediaBytes).digest('hex');
 await fs.writeFile(dir+'/inventory.json',JSON.stringify({capturedAt,root:cfg.sourceRoot,files:python,services,mediaBefore,build:JSON.parse(await fs.readFile(cfg.sourceRoot+'/dist-server/build.json','utf8'))}));
 return{pid:process.pid,capturedAt,bots:running.length,groups:ais.length,protocolOwners:processes.length,fresh:true};
}
