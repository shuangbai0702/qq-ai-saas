import {randomUUID} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import path from 'node:path';
import lockfile from 'proper-lockfile';
import {EgressSchema,type QQEgress} from '../egress.js';

export interface NapcatSettings {url:string;webuiToken:string;launcher?:string;args?:string[];runtimeDir?:string;egress?:QQEgress}

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
