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

const marker=Symbol.for('fishbot.autonomous.maintenance');
export async function installAutonomousRuntime(agent:NodeAgent,release:string){
 if(!/^[a-f0-9]{64}$/.test(release)||agent.closed)throw new Error('Invalid maintenance owner or release');
 const live=agent as any,previous=live[marker];
 if(previous?.release===release)return{unchanged:true,pid:process.pid,release};
 const timer=agent.timer;if(timer)clearInterval(timer);agent.timer=undefined;
 let installed:AutonomyCoordinator|undefined;
 const prototype=Object.getPrototypeOf(agent);
 const old={start:agent.host.start,apply:agent.ai.applyConfig,attach:agent.shared.attach,health:prototype.health,context:prototype.context,autonomy:agent.autonomy,shutdown:previous?.originalShutdown||agent.shutdown};
 const children=[...agent.host.children].map(([id,child])=>({id,child,pid:child.pid}));
 try{
  const deadline=Date.now()+20000;
  while(agent.busy&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,20));
  if(agent.busy||agent.closed)throw new Error('Agent did not reach a maintenance boundary');
  await old.autonomy?.stop();
  // These functions are bundled with the release, so an already loaded ESM
  // cache cannot silently reinstall yesterday's attach/provision/start code.
  agent.host.start=ProcessHost.prototype.start;
  agent.ai.applyConfig=AstrBotAdapter.prototype.applyConfig;
  agent.shared.attach=SharedAstrBotAdapter.prototype.attach;
  prototype.health=NodeAgent.prototype.health;
  prototype.context=NodeAgent.prototype.context;
  installed=new AutonomyCoordinator(agent.db,runtimeClient(agent.db));agent.autonomy=installed;
  agent.shutdown=async()=>{await agent.autonomy.stop();return old.shutdown.call(agent)};
  for(const owned of children){if(agent.host.children.get(owned.id)!==owned.child||owned.child.pid!==owned.pid||owned.child.exitCode!==null)throw new Error('A child owner changed during maintenance')}
  live[marker]={release,originalShutdown:old.shutdown,at:new Date().toISOString()};
  installed.start();
  return{pid:process.pid,release,preservedChildren:children.map(({id,pid})=>({id,pid})),coordinator:installed.owner};
 }catch(error){
  await installed?.stop();agent.host.start=old.start;agent.ai.applyConfig=old.apply;agent.shared.attach=old.attach;prototype.health=old.health;prototype.context=old.context;agent.shutdown=old.shutdown;
  // A coordinator that was already stopped is not restarted with stale work.
  // The passive runtime and all protocol owners remain available on failure.
  if(old.autonomy)agent.autonomy=old.autonomy;
  throw error;
 }finally{
  if(timer&&!agent.closed){agent.timer=setInterval(()=>void agent.tick().catch(error=>console.error('agent tick:',redact(String(error)))),500);agent.timer.unref()}
 }
}
