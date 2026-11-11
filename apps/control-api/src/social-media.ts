import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {mkdir,readFile,readdir,writeFile,rename,rm} from 'node:fs/promises';
import {validatePng} from './png.js';
import {registerMemePacks} from './meme-packs.js';

// Tenant-owned uploads only. Installed animated packs have their own routes;
// the retired three base reactions must never be exposed or re-enabled here.
export function registerSocialMedia(app:FastifyInstance,root:string,owned:(req:any,id:string)=>Promise<any>){
 registerMemePacks(app,root,owned);
 for(const kind of ['reactions','gallery']){
 const prefix=kind==='gallery'?'gal_':'usr_',limit=kind==='gallery'?10:5;
 const pattern=new RegExp('^'+prefix+'[a-f0-9]{16}$');
 const locks=new Map<string,Promise<unknown>>();
 const directory=(id:string)=>path.join(root,'bots',id,kind);
 const invalid=(message:string,code=400)=>Object.assign(new Error(message),{statusCode:code});
 async function files(id:string){try{return(await readdir(directory(id))).filter(n=>n.endsWith('.json')&&pattern.test(n.slice(0,-5)))}catch(e:any){if(e.code==='ENOENT')return[];throw e}}
 async function locked<T>(id:string,fn:()=>Promise<T>):Promise<T>{const before=locks.get(id)||Promise.resolve();const task=before.catch(()=>{}).then(fn);locks.set(id,task);try{return await task}finally{if(locks.get(id)===task)locks.delete(id)}}
 const version=(raw:string)=>createHash('sha256').update(raw).digest('hex');
 async function metadata(bot:string,id:string){return readFile(path.join(directory(bot),id+'.json'),'utf8')}
 app.get<{Params:{id:string}}>(`/v1/bots/:id/${kind}`,async req=>{await owned(req,req.params.id);return locked(req.params.id,async()=>{const items=[];const ids=(await files(req.params.id)).map(n=>n.slice(0,-5));for(const id of ids){const raw=await metadata(req.params.id,id),item=JSON.parse(raw);items.push({id:item.id,meaning:item.meaning,avoid:item.avoid,enabled:item.enabled!==false,version:version(raw)})}return{items,limit}})});
 app.patch<{Params:{id:string;image:string}}>(`/v1/bots/:id/${kind}/:image`,async req=>{
  await owned(req,req.params.id);if(!pattern.test(req.params.image))throw invalid('图片不存在',404);
  const value=z.object({expectedVersion:z.string().length(64),meaning:z.string().trim().min(2).max(100),avoid:z.string().max(100),enabled:z.boolean()}).parse(req.body);
  return locked(req.params.id,async()=>{const file=path.join(directory(req.params.id),req.params.image+'.json');let raw:string;try{raw=await metadata(req.params.id,req.params.image)}catch(e:any){if(e.code==='ENOENT')throw invalid('图片不存在',404);throw e}
   if(version(raw)!==value.expectedVersion)throw invalid('图片说明已更新，请刷新后重试',409);
   const old=JSON.parse(raw),temporary=path.join(directory(req.params.id),randomUUID()+'.tmp');
   await mkdir(directory(req.params.id),{recursive:true});try{await writeFile(temporary,JSON.stringify({...old,meaning:value.meaning,avoid:value.avoid,enabled:value.enabled}),{flag:'wx',mode:0o600});await rename(temporary,file)}finally{await rm(temporary,{force:true})}return{ok:true};
  });
 });
 app.post<{Params:{id:string}}>(`/v1/bots/:id/${kind}`,{bodyLimit:360000,config:{rateLimit:{max:10,timeWindow:'1 minute'}}},async req=>{
  await owned(req,req.params.id);const data=z.object({png:z.string().regex(/^[A-Za-z0-9+/]+={0,2}$/).max(350000),meaning:z.string().trim().min(2).max(100),avoid:z.string().max(100).default('')}).parse(req.body);
  const bytes=Buffer.from(data.png,'base64');await validatePng(bytes);
  const id=prefix+createHash('sha256').update(bytes).digest('hex').slice(0,16),dir=directory(req.params.id);
  return locked(req.params.id,async()=>{const list=await files(req.params.id);if(!list.includes(id+'.json')&&list.length>=limit)throw invalid('本分类最多'+limit+'张图片，请先删除旧图');await mkdir(dir,{recursive:true});
   const temporary=path.join(dir,randomUUID()+'.tmp'),image=path.join(dir,id+'.png'),existing=list.includes(id+'.json');let installed=false;
   try{if(!existing){try{await writeFile(image,bytes,{mode:0o600,flag:'wx'});installed=true}catch(e:any){if(e.code!=='EEXIST')throw e}}await writeFile(temporary,JSON.stringify({id,meaning:data.meaning,avoid:data.avoid}),{mode:0o600,flag:'wx'});await rename(temporary,path.join(dir,id+'.json'));return{id};}
   catch(e){if(installed)await rm(image,{force:true});throw e}finally{await rm(temporary,{force:true})}});
 });
 app.get<{Params:{id:string;image:string}}>(`/v1/bots/:id/${kind}/:image`,async(req,reply)=>{await owned(req,req.params.id);if(!pattern.test(req.params.image))throw invalid('图片不存在',404);const dir=directory(req.params.id);try{await metadata(req.params.id,req.params.image);return reply.header('Cache-Control','private, no-store').type('image/png').send(await readFile(path.join(dir,req.params.image+'.png')))}catch(e:any){if(e.code==='ENOENT')throw invalid('图片不存在',404);throw e}});
 app.delete<{Params:{id:string;image:string}}>(`/v1/bots/:id/${kind}/:image`,async req=>{await owned(req,req.params.id);if(!pattern.test(req.params.image))throw invalid('图片不存在',404);return locked(req.params.id,async()=>{for(const ext of ['json','png'])await rm(path.join(directory(req.params.id),req.params.image+'.'+ext),{force:true});return{ok:true}})});
}
}
