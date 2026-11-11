/** Bounded in-process maintenance for deployments that must retain QQ pipes.
 *
 * Existing ChildProcess objects and their stdout/stderr owners must survive.
 * Only the autonomous coordinator and the affected canonical methods are
 * replaced. No process, database connection or account is recreated here.
 */
import {NodeAgent} from './agent.js';
import {AutonomyCoordinator,runtimeClient} from './autonomous.js';
import {ProcessHost} from './process-host.js';
import {AstrBotAdapter} from '../../../adapters/astrbot/index.js';
import {SharedAstrBotAdapter} from '../../../adapters/astrbot/shared.js';
import {redact} from '../../../packages/shared/security.js';
import {FavoriteMaintenance} from './favorite-maintenance.js';

// Shutdown stays with the canonical Agent, which dynamically owns all three
// maintenance workers in the verified deployment baseline.
// Old live Agents may predate the bounded operation lanes.
const controlBusy=(agent:NodeAgent)=>agent.controlIdle?!agent.controlIdle():agent.busy;

/** Refresh only trusted frontend configuration transport; retain all owners. */
export async function installForegroundContextRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid foreground release');
 const marker=Symbol.for('fishbot.foreground.context'),live=agent as any;
 if(live[marker]?.release===release)return{unchanged:true,pid:process.pid,release};
 const timer=agent.timer;if(timer)clearInterval(timer);agent.timer=undefined;
 const prototype=Object.getPrototypeOf(agent);
 const previous={context:prototype.context,apply:agent.ai.applyConfig,attach:agent.shared.attach};
 const children=[...agent.host.children].map(([id,child])=>({id,child,pid:child.pid}));
 try{
  const deadline=Date.now()+20000;
  while(controlBusy(agent)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,20));
  if(controlBusy(agent)||agent.closed)throw new Error('Agent did not reach foreground maintenance boundary');
  prototype.context=NodeAgent.prototype.context;
  agent.ai.applyConfig=AstrBotAdapter.prototype.applyConfig;
  agent.shared.attach=SharedAstrBotAdapter.prototype.attach;
  for(const owned of children)if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('Foreground maintenance changed child owner');
  live[marker]={release,at:Date.now()};
  return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid}))};
 }catch(error){prototype.context=previous.context;agent.ai.applyConfig=previous.apply;agent.shared.attach=previous.attach;throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(()=>{}),500);agent.timer.unref()}}
}

/** Update the existing maintenance owner without replacing protocol children. */
export async function installFavoriteRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid favorite maintenance release');
 const marker=Symbol.for('fishbot.favorite.maintenance'),live=agent as any;
 if(live[marker]?.release===release&&!live.favorites?.closed)return{unchanged:true,pid:process.pid,release};
 const timer=agent.timer,originalTick=agent.tick,previous=live.favorites;
 const existing=live[marker]?.hooks,hooks=existing??{current:previous};
 if(timer)clearInterval(timer);agent.timer=undefined;
 const children=[...agent.host.children].map(([id,child])=>({id,child,pid:child.pid}));
 let installed:FavoriteMaintenance|undefined;
 try{
  const deadline=Date.now()+20000;while(controlBusy(agent)&&Date.now()<deadline)await new Promise(r=>setTimeout(r,20));
  if(controlBusy(agent)||agent.closed)throw new Error('Agent did not reach a favorite maintenance boundary');
  await previous?.stop();installed=new FavoriteMaintenance(agent.db,agent.root,agent.key);
  for(const owned of children)if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('Protocol owner changed during favorite maintenance');
  live.favorites=installed;hooks.current=installed;
  if(!existing){
   agent.tick=async()=>{await originalTick.call(agent);if(!agent.closed)hooks.current.tick()};
  }
  live[marker]={release,at:Date.now(),hooks};
  return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid}))};
 }catch(error){await installed?.stop();live.favorites=previous;hooks.current=previous;agent.tick=originalTick;if(previous)previous.closed=false;throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}}
}

const marker=Symbol.for('fishbot.autonomous.maintenance');
export async function installAutonomousRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid maintenance owner or release');
 const live=agent as any,previous=live[marker];
 if(previous?.release===release&&!agent.autonomy.closed)return{unchanged:true,pid:process.pid,release};
 const timer=agent.timer;if(timer)clearInterval(timer);agent.timer=undefined;
 let installed:AutonomyCoordinator|undefined;
 const prototype=Object.getPrototypeOf(agent);
 const old={start:agent.host.start,apply:agent.ai.applyConfig,attach:agent.shared.attach,health:prototype.health,context:prototype.context,autonomy:agent.autonomy};
 const wasAutonomyRunning=!old.autonomy.closed&&Boolean(old.autonomy.timer);
 const children=[...agent.host.children].map(([id,child])=>({id,child,pid:child.pid}));
 try{
  const deadline=Date.now()+20000;
  while(controlBusy(agent)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,20));
  if(controlBusy(agent)||agent.closed)throw new Error('Agent did not reach a maintenance boundary');
  await old.autonomy?.stop();
  // These functions are bundled with the release, so an already loaded ESM
  // cache cannot silently reinstall yesterday's attach/provision/start code.
  agent.host.start=ProcessHost.prototype.start;
  agent.ai.applyConfig=AstrBotAdapter.prototype.applyConfig;
  agent.shared.attach=SharedAstrBotAdapter.prototype.attach;
  prototype.health=NodeAgent.prototype.health;
  prototype.context=NodeAgent.prototype.context;
  installed=new AutonomyCoordinator(agent.db,runtimeClient(agent.db));agent.autonomy=installed;
  for(const owned of children){if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('A child owner changed during maintenance')}
  live[marker]={release,at:new Date().toISOString()};
  installed.start();
  return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid})),coordinator:installed.owner};
 }catch(error){
  await installed?.stop();agent.host.start=old.start;agent.ai.applyConfig=old.apply;agent.shared.attach=old.attach;prototype.health=old.health;prototype.context=old.context;
  // stop() has settled the old active Work before replacement. On failure,
  // restore the previous scheduler only if it was running; never replay a Work.
  if(old.autonomy){agent.autonomy=old.autonomy;if(wasAutonomyRunning&&!agent.closed){old.autonomy.closed=false;old.autonomy.start()}}
  throw error;
 }finally{
  if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}
 }
}


/** Install only Bilibili jobs into the existing Agent. Keeps child pipe owners,
 * heartbeat wrapper and the current autonomous coordinator unchanged. */
export async function installBilibiliRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid Bilibili maintenance release');
 const marker=Symbol.for('fishbot.bilibili.maintenance'),live=agent as any;
 if(live[marker]?.release===release&&!live.bilibili?.stopped)return{unchanged:true,pid:process.pid,release};
 const {BilibiliExecutor}=await import('./bilibili/executor.js');
 const timer=agent.timer,originalTick=agent.tick,previous=live.bilibili;
 // Reuse one mutable owner across releases. Repeated maintenance must not
 // retain every old executor through another tick wrapper each time.
 const existingHooks=live[marker]?.hooks;
 const hooks=existingHooks??{current:previous};
 if(timer)clearInterval(timer);agent.timer=undefined;
 const children=[...agent.host.children].map(([id,child])=>({id,child,pid:child.pid}));
 let installed:InstanceType<typeof BilibiliExecutor>|undefined;
 try{
  const deadline=Date.now()+20000;while(controlBusy(agent)&&Date.now()<deadline)await new Promise(r=>setTimeout(r,20));
  if(controlBusy(agent)||agent.closed)throw new Error('Agent did not reach a Bilibili maintenance boundary');
  await previous?.stop();installed=new BilibiliExecutor(agent.db,agent.root,agent.key);await installed.recover();
  await installed.publishBridges();live.bilibili=installed;
  // Calling an existing native tick is harmless: executor mutex/nextRun prevent
  // double admission. The original heartbeat closure and pipe drainers survive.
  hooks.current=installed;
  if(!existingHooks){
   agent.tick=async()=>{await originalTick.call(agent);if(!agent.closed)hooks.current.tick()};
  }
  for(const owned of children)if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('Runtime owner changed during Bilibili maintenance');
  live[marker]={release,at:Date.now(),hooks};return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid})),autonomousCoordinator:agent.autonomy.owner};
 }catch(error){await installed?.stop();hooks.current=previous;agent.tick=originalTick;live.bilibili=previous;if(previous)previous.stopped=false;throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}}
}
