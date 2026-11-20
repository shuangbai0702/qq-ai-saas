/** Bounded in-process maintenance for deployments that must retain QQ pipes.
 *
 * Existing ChildProcess objects and their stdout/stderr owners must survive.
 * Only the autonomous coordinator and the affected canonical methods are
 * replaced. No process, database connection or account is recreated here.
 */
import {NodeAgent,HeartbeatWriter} from './agent.js';
import {AutonomyCoordinator} from './autonomous.js';
import {runtimeClient} from '../../../packages/shared/runtime-client.js';
import {ProcessHost} from './process-host.js';
import {AstrBotAdapter} from '../../../adapters/astrbot/index.js';
import {SharedAstrBotAdapter} from '../../../adapters/astrbot/shared.js';
import {redact} from '../../../packages/shared/security.js';
import {FavoriteMaintenance} from './favorite-maintenance.js';
import {DshHost} from './dsh-host.js';
import {BilibiliExecutor} from './bilibili/executor.js';
import {createFavoriteCaptioner} from '../../../packages/shared/qq-favorite-semantics.js';
import {resolvePlatformModel} from '../../../packages/shared/platform-model.js';
import {NapCatAdapter} from '../../../adapters/protocol/napcat/index.js';

/** QR-only patch: preserve the Agent, heartbeat, protocol pipes and AI owners. */
export async function installQrSessionRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid QR session release');
 const timer=agent.timer;if(timer)clearInterval(timer);agent.timer=undefined;
 const names=['recoverLoginSession','pollLogin','healthSnapshot'] as const;
 const previous=names.map(name=>Object.getOwnPropertyDescriptor(agent,name));
 try{
  const deadline=Date.now()+20000;while(controlBusy(agent)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,20));
  if(controlBusy(agent)||agent.closed)throw new Error('QR session boundary is busy');
  for(const name of names)Object.defineProperty(agent,name,{value:NodeAgent.prototype[name],writable:true,configurable:true});
  return{pid:process.pid,release,processOwnersPreserved:true,methods:names};
 }catch(error){names.forEach((name,i)=>{if(previous[i])Object.defineProperty(agent,name,previous[i]!);else delete (agent as any)[name]});throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}}
}

/** Preserve independent QQ owners while replacing their control methods. */
export async function installQqRecoveryRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid QQ recovery release');
 const timer=agent.timer;if(timer)clearInterval(timer);agent.timer=undefined;
 const changes=[{target:agent,name:'execute',next:NodeAgent.prototype.execute},
  {target:agent,name:'healthSnapshot',next:NodeAgent.prototype.healthSnapshot},
  {target:agent.shared,name:'assign',next:SharedAstrBotAdapter.prototype.assign},
  ...['getLoginState','getHealth'].map(name=>({target:agent.protocols.napcat,name,next:(NapCatAdapter.prototype as any)[name]}))];
 const previous=changes.map(({target,name})=>Object.getOwnPropertyDescriptor(target,name));
 try{
  const deadline=Date.now()+20000;while(controlBusy(agent)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,20));
  if(controlBusy(agent)||agent.closed)throw new Error('QQ recovery boundary is busy');
  for(const {target,name,next} of changes){if(!target||typeof (target as any)[name]!=='function')throw new Error('QQ recovery method contract changed');Object.defineProperty(target,name,{value:next,writable:true,configurable:true})}
  return{pid:process.pid,release,processOwnersPreserved:true};
 }catch(error){changes.forEach(({target,name},i)=>{if(previous[i])Object.defineProperty(target,name,previous[i]!);else delete (target as any)[name]});throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}}
}

/** Install lifecycle methods without replacing the live Agent or QQ owners. */
export function installProtocolLifecycleRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed||controlBusy(agent)||(agent as any).shutdownTask)throw new Error('Invalid protocol lifecycle boundary');
 const agentPrototype=Object.getPrototypeOf(agent),hostPrototype=Object.getPrototypeOf(agent.host);
 const methods=[{target:agentPrototype,source:NodeAgent.prototype,name:'shutdownOnce'},
  ...['helper','start','stop'].map(name=>({target:hostPrototype,source:ProcessHost.prototype,name}))];
 const updates=methods.map(method=>({...method,descriptor:Object.getOwnPropertyDescriptor(method.source,method.name)}));
 if(updates.some(method=>typeof method.descriptor?.value!=='function'||
  (Object.getOwnPropertyDescriptor(method.target,method.name)?.configurable===false)||!Object.isExtensible(method.target)))throw new Error('Protocol lifecycle method contract changed');
 for(const method of updates)Object.defineProperty(method.target,method.name,method.descriptor!);
 return{pid:process.pid,release,preservedChildren:[...agent.host.children].map(([id,child])=>({id,pid:child.pid}))};
}

/** Add the native delegation host without replacing QQ owners or the heartbeat. */
export async function installDshRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed||controlBusy(agent))throw new Error('Invalid DSH maintenance boundary');
 if(agent.dsh)throw new Error('DSH already installed');
 const host=new DshHost(agent);await host.start();
 if(agent.closed){await host.stop();throw new Error('DSH maintenance owner changed')}
 agent.dsh=host;
 const previous=agent.tick,shutdown=agent.shutdown;let published=0;
 (agent as any)[Symbol.for('fishbot.dsh.maintenance')]={release,host,previous,shutdown};
 agent.tick=async()=>{await previous.call(agent);if(!agent.closed&&Date.now()-published>3000){published=Date.now();await host.publish()}};
 agent.shutdown=async()=>{try{await host.stop()}finally{await shutdown.call(agent)}};
 return{pid:process.pid,release,enabled:true};
}
export async function uninstallDshRuntime(agent:NodeAgent,release:string){
 const marker=Symbol.for('fishbot.dsh.maintenance'),installed=(agent as any)[marker];
 if(!installed||installed.release!==release||agent.dsh!==installed.host)throw new Error('DSH rollback owner mismatch');
 await installed.host.stop();agent.dsh=undefined;agent.tick=installed.previous;agent.shutdown=installed.shutdown;delete (agent as any)[marker];
 return{pid:process.pid,release,enabled:false};
}

// Shutdown stays with the canonical Agent, which dynamically owns all three
// maintenance workers in the verified deployment baseline.
const controlBusy=(agent:NodeAgent)=>!agent.controlIdle();

/** Activate the platform connection on the live Agent while preserving QQ and Worker ownership. */
export function installPlatformModelRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid platform model release');
 const marker=Symbol.for('fishbot.platform-model.maintenance');
 const live=agent as any;
 if(live[marker]?.release===release)return {pid:process.pid,release,unchanged:true};
 if(live[marker])throw new Error('A platform model release already owns this Agent');
 const context=agent.context, favorites=live.favorites,caption=favorites?.caption;
 if(typeof caption!=='function')throw new Error('Favorite annotation owner is unavailable');
 const newCaption=createFavoriteCaptioner(agent.db,agent.key,agent.root);
 agent.context=async bot=>{
  const current=await context.call(agent,bot);
  const [revision]=await agent.db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);
  return revision?.config?.platformModel===true?{...current,...resolvePlatformModel(current.config,agent.key,bot)}:current;
 };
 favorites.caption=newCaption;
 live[marker]={release,context,favorites,caption};
 return {pid:process.pid,release,children:agent.host.children.size};
}

export function uninstallPlatformModelRuntime(agent:NodeAgent,release:string){
 const marker=Symbol.for('fishbot.platform-model.maintenance');
 const installed=(agent as any)[marker];
 if(!installed||installed.release!==release)throw new Error('Platform model release owner changed');
 agent.context=installed.context;
 installed.favorites.caption=installed.caption;
 delete (agent as any)[marker];
 return {pid:process.pid,release,children:agent.host.children.size};
}

/** Refresh these two stateless methods while the existing owners are held.
 * No constructor, timer, database, ChildProcess or heartbeat queue is replaced.
 */
export function installResilienceRuntime(agent:NodeAgent,release:string,heartbeatPrototype:object){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed||controlBusy(agent))throw new Error('Invalid resilience maintenance boundary');
 const descriptor=Object.getOwnPropertyDescriptor(HeartbeatWriter.prototype,'writeOnce');
 if(!descriptor||!Object.getOwnPropertyDescriptor(heartbeatPrototype,'writeOnce'))throw new Error('Heartbeat owner contract changed');
 Object.getPrototypeOf(agent).execute=NodeAgent.prototype.execute;
 Object.defineProperty(heartbeatPrototype,'writeOnce',descriptor);
 return{pid:process.pid,release,agentOwnerPreserved:true,heartbeatQueuePreserved:true};
}

/** Refresh process inspection only; keep Agent, QQ, AI and existing queues. */
export async function installProcessInspectionRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid process inspection release');
 const timer=agent.timer;if(timer)clearInterval(timer);agent.timer=undefined;
 const host=agent.host as any,names=['helper','beginInspection','inspect'],previous=names.map(name=>host[name]);
 try{
  const deadline=Date.now()+20000;
  while(controlBusy(agent)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,20));
  if(controlBusy(agent)||agent.closed)throw new Error('Agent did not reach process inspection boundary');
  for(const name of names)host[name]=(ProcessHost.prototype as any)[name];
  return{pid:process.pid,release,agentOwnerPreserved:true,processOwnersPreserved:true};
 }catch(error){names.forEach((name,index)=>host[name]=previous[index]);throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}}
}

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
 const timer=agent.timer,previous=live.favorites;
 if(timer)clearInterval(timer);agent.timer=undefined;
 const children=[...agent.host.children].map(([id,child])=>({id,child,pid:child.pid}));
 let installed:FavoriteMaintenance|undefined;
 try{
  const deadline=Date.now()+20000;while(controlBusy(agent)&&Date.now()<deadline)await new Promise(r=>setTimeout(r,20));
  if(controlBusy(agent)||agent.closed)throw new Error('Agent did not reach a favorite maintenance boundary');
  await previous?.stop();installed=new FavoriteMaintenance(agent.db,agent.root,agent.key);
  for(const owned of children)if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('Protocol owner changed during favorite maintenance');
  live.favorites=installed;
  live[marker]={release,at:Date.now()};
  return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid}))};
 }catch(error){await installed?.stop();live.favorites=previous;if(previous)previous.closed=false;throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}}
}

const marker=Symbol.for('fishbot.autonomous.maintenance');
export async function installAutonomousRuntime(agent:NodeAgent,release:string,options:{start?:boolean}={}){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid maintenance owner or release');
 const live=agent as any,previous=live[marker];
 if(previous?.release===release&&!agent.autonomy.closed)return{unchanged:true,pid:process.pid,release};
 const timer=agent.timer;if(timer)clearInterval(timer);agent.timer=undefined;
 let installed:AutonomyCoordinator|undefined;
 const prototype=Object.getPrototypeOf(agent);
 const old={start:agent.host.start,apply:agent.ai.applyConfig,attach:agent.shared.attach,request:agent.shared.request,health:prototype.health,context:prototype.context,autonomy:agent.autonomy};
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
  agent.shared.request=SharedAstrBotAdapter.prototype.request;
  prototype.health=NodeAgent.prototype.health;
  prototype.context=NodeAgent.prototype.context;
  installed=new AutonomyCoordinator(agent.db,runtimeClient(agent.db));agent.autonomy=installed;
  for(const owned of children){if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('A child owner changed during maintenance')}
  live[marker]={release,at:new Date().toISOString()};
  // A release may replace all owners while the existing Agent remains held.
  // Do not start the immediate coordinator tick until that release resumes.
  if(options.start!==false)installed.start();else installed.closed=true;
  return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid})),coordinator:installed.owner};
 }catch(error){
  await installed?.stop();agent.host.start=old.start;agent.ai.applyConfig=old.apply;agent.shared.attach=old.attach;agent.shared.request=old.request;prototype.health=old.health;prototype.context=old.context;
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
export async function installBilibiliRuntime(agent:NodeAgent,release:string,options:{start?:boolean}={}){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid Bilibili maintenance release');
 const marker=Symbol.for('fishbot.bilibili.maintenance'),live=agent as any;
 if(live[marker]?.release===release&&live.bilibili?.stopped===(options.start===false))return{unchanged:true,pid:process.pid,release};
 const timer=agent.timer,previous=live.bilibili,wasStopped=previous?.stopped;
 if(timer)clearInterval(timer);agent.timer=undefined;
 const children=[...agent.host.children].map(([id,child])=>({id,child,pid:child.pid}));
 let installed:InstanceType<typeof BilibiliExecutor>|undefined;
 try{
  const deadline=Date.now()+20000;while(controlBusy(agent)&&Date.now()<deadline)await new Promise(r=>setTimeout(r,20));
  if(controlBusy(agent)||agent.closed)throw new Error('Agent did not reach a Bilibili maintenance boundary');
  await previous?.stop();installed=new BilibiliExecutor(agent.db,agent.root,agent.key);await installed.recover();
  await installed.publishBridges();installed.stopped=options.start===false;live.bilibili=installed;
  for(const owned of children)if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('Runtime owner changed during Bilibili maintenance');
  live[marker]={release,at:Date.now()};return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid})),autonomousCoordinator:agent.autonomy.owner};
 }catch(error){await installed?.stop();live.bilibili=previous;if(previous)previous.stopped=wasStopped;throw error}
 finally{if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}}
}
