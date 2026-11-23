import {randomInt,randomUUID} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import path from 'node:path';
import lockfile from 'proper-lockfile';
import {z} from 'zod';

const ProcessIdentity=z.object({pid:z.number().int().positive(),created:z.number().positive(),generation:z.number().int().nonnegative(),online:z.boolean(),counted:z.boolean()});
const EgressSchema=z.object({route:z.enum(['direct','huawei']),switches:z.number().int().min(0).max(104),failures:z.number().int().nonnegative(),pending:z.boolean(),process:ProcessIdentity.optional()});
export type NapcatEgress=z.infer<typeof EgressSchema>;
export interface NapcatSettings {url:string;webuiToken:string;launcher?:string;args?:string[];runtimeDir?:string;egress?:NapcatEgress}

// The existing per-Bot NapCat settings file owns route selection and counters.
// Holding this lock also serializes health receipts with protocol admission.
export async function withNapcatSettings<T>(file:string,edit:(settings:NapcatSettings|undefined,save:(settings:NapcatSettings)=>Promise<void>)=>Promise<{settings:NapcatSettings;result:T}>):Promise<T>{
 await mkdir(path.dirname(file),{recursive:true});
 const release=await lockfile.lock(file,{realpath:false,stale:60000,retries:{retries:15,minTimeout:100,maxTimeout:500}});
 const temp=file+'.'+randomUUID()+'.tmp';
 try{
  let settings:NapcatSettings|undefined;
  try{settings=JSON.parse(await readFile(file,'utf8'));if(settings?.egress)settings.egress=EgressSchema.parse(settings.egress)}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error}
  let saved=JSON.stringify(settings);
  const save=async(value:NapcatSettings)=>{if(JSON.stringify(value)!==saved){await writeFile(temp,JSON.stringify(value,null,2),{mode:0o600,flag:'wx'});await rename(temp,file);saved=JSON.stringify(value)}};
  const next=await edit(settings,save);await save(next.settings);
  return next.result;
 }finally{await rm(temp,{force:true});await release()}
}

export function proxyPort(env:NodeJS.ProcessEnv=process.env):number|undefined{
 const value=env.NAPCAT_EGRESS_PROXY_PORT;
 if(value===undefined||value==='')return undefined;
 if(!/^[1-9]\d{0,4}$/.test(value)||Number(value)>65535)throw new Error('NAPCAT_EGRESS_PROXY_PORT 必须为本机代理端口');
 return Number(value);
}
export function initialEgress(choose=()=>randomInt(2)):NapcatEgress{return{route:choose()===0?'direct':'huawei',switches:0,failures:0,pending:false}}
export function nextEgress(state:NapcatEgress):NapcatEgress{
 if(!state.pending)return state;
 if(state.switches===104)throw new Error('出口切换计数已达到可精确表示上限');
 return{route:state.route==='direct'?'huawei':'direct',switches:state.switches+1,failures:0,pending:false};
}
export function observeEgress(state:NapcatEgress,identity:{pid:number;created:number;generation:number},online:boolean,kicked:boolean):NapcatEgress{
 const active=state.process;
 if(!active||active.pid!==identity.pid||active.created!==identity.created||active.generation!==identity.generation)return state;
 if(online)return{...state,process:{...active,online:true,counted:false}};
 if(!kicked||!active.online||active.counted)return state;
 const failures=state.failures+1;
 return{...state,failures,pending:failures>=2**Math.floor(state.switches/2),process:{...active,counted:true}};
}
