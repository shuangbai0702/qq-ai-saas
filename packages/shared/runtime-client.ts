import {allowsInFlightBaseRevision} from './web-base-revision.js';
import type {Sql} from '../database/index.js';
import {runtimeIpc} from './runtime-ipc.js';

export type RuntimeCall=(bot:any,operation:string,body:unknown,timeoutMs?:number)=>Promise<any>;

/** Existing authenticated member IPC; model/receipt callers share one route. */
export function runtimeClient(db:Sql):RuntimeCall{
 return async(bot,operation,body,timeoutMs=3000)=>{
  const [current]=await db.query('SELECT runtime_mode,group_id,generation,revision FROM bots WHERE id=$1 AND tenant_id=$2 AND deleted_at IS NULL',[bot.bot_id,bot.tenant_id]);
  if(!current||current.generation!==bot.generation)throw new Error('runtime_identity_changed');
  if(current.revision!==bot.revision){
   if(operation!=='tool'||!await allowsInFlightBaseRevision(db,bot.bot_id,bot.tenant_id,bot.revision,current.revision))throw new Error('runtime_identity_changed');
   body={...(body as Record<string,unknown>),requestRevision:bot.revision};
  }
  let port:number,route:string,token:string,groupGeneration:number|undefined;
  if(current.runtime_mode==='shared'){
   const [g]=await db.query('SELECT g.* FROM runtime_groups g JOIN group_members m ON m.group_id=g.id WHERE m.bot_id=$1',[bot.bot_id]);if(!g)throw new Error('runtime_group_missing');
   port=g.health_port;route=`/members/${bot.bot_id}/autonomous/${operation}`;token=g.token;groupGeneration=g.generation;
  }else{
   const [i]=await db.query('SELECT ai_port,token FROM instances WHERE bot_id=$1',[bot.bot_id]);if(!i)throw new Error('runtime_instance_missing');
   port=i.ai_port+1;route=`/autonomous/${operation}`;token=i.token;
  }
  const headers:Record<string,string>={Authorization:'Bearer '+token,'Content-Type':'application/json','X-Member-Generation':String(bot.generation),'X-Member-Revision':String(current.revision)};
  if(groupGeneration!==undefined)headers['X-Group-Generation']=String(groupGeneration);
  try{
   const response=await runtimeIpc(port,route,{method:'POST',headers,body,timeoutMs});
   if(!response.ok)throw new Error('autonomous_ipc_'+response.status);return await response.json();
  }catch(error){
   // Preserve the original classification, but identify the failed control
   // operation without retaining request bodies, credentials or destinations.
   if(error instanceof Error)Object.assign(error,{runtimeOperation:operation});
   throw error;
  }
 };
}
