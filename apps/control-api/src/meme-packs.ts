import type {FastifyInstance} from 'fastify';import {readFile,mkdir,writeFile,rename,rm} from 'node:fs/promises';import path from 'node:path';import {createHash,randomUUID} from 'node:crypto';import {z} from 'zod';
const packRoot=path.resolve('templates/meme-packs/blue-fat-fish');
export async function installedFishPack(){
 try{const pack=JSON.parse(await readFile(path.join(packRoot,'index.json'),'utf8'));return {...pack,items:pack.items.filter((i:any)=>/^bf_[a-f0-9]{16}$/.test(i.id)&&i.file===i.id+'.gif').slice(0,200)}}catch(e:any){if(e.code==='ENOENT')return {id:'blue-fat-fish',name:'蓝色大肥鱼表情包',items:[]};throw e}
}
export function registerMemePacks(app:FastifyInstance,root:string,owned:(req:any,id:string)=>Promise<any>){
 const locks=new Map<string,Promise<unknown>>(),hash=(s:string)=>createHash('sha256').update(s).digest('hex');
 const file=(id:string)=>path.join(root,'bots',id,'meme-pack-settings.json');
 async function settings(id:string){try{return await readFile(file(id),'utf8')}catch(e:any){if(e.code==='ENOENT')return '{"disabled":[]}';throw e}}
 app.get<{Params:{id:string}}>('/v1/bots/:id/meme-packs',async req=>{await owned(req,req.params.id);const p=await installedFishPack(),raw=await settings(req.params.id),disabled=JSON.parse(raw).disabled;return{packs:[{...p,installed:p.items.length>0,version:hash(raw),items:p.items.map((i:any)=>({...i,enabled:!disabled.includes(i.id)}))}]}});
 app.get<{Params:{id:string;image:string}}>('/v1/bots/:id/meme-packs/blue-fat-fish/:image',async(req,reply)=>{await owned(req,req.params.id);const p=await installedFishPack(),item=p.items.find((i:any)=>i.id===req.params.image);if(!item)throw Object.assign(new Error('表情不存在'),{statusCode:404});const bytes=await readFile(path.join(packRoot,item.file));if(bytes.length>1048576)throw new Error('Pack media exceeds verified limit');return reply.header('Cache-Control','private, no-store').type('image/gif').send(bytes)});
 app.patch<{Params:{id:string;image:string}}>('/v1/bots/:id/meme-packs/blue-fat-fish/:image',async req=>{
  await owned(req,req.params.id);const value=z.object({enabled:z.boolean(),expectedVersion:z.string().length(64)}).parse(req.body),id=req.params.id;
  const previous=locks.get(id)||Promise.resolve();const task=previous.catch(()=>{}).then(async()=>{
   const pack=await installedFishPack();if(!pack.items.some((i:any)=>i.id===req.params.image))throw Object.assign(new Error('表情不存在'),{statusCode:404});
   const raw=await settings(id);if(hash(raw)!==value.expectedVersion)throw Object.assign(new Error('图包状态已更新，请刷新后重试'),{statusCode:409});
   const disabled=new Set<string>(JSON.parse(raw).disabled);if(value.enabled)disabled.delete(req.params.image);else disabled.add(req.params.image);
   const dest=file(id),temp=dest+'.'+randomUUID()+'.tmp';await mkdir(path.dirname(dest),{recursive:true});try{await writeFile(temp,JSON.stringify({disabled:[...disabled]}),{mode:0o600,flag:'wx'});await rename(temp,dest)}finally{await rm(temp,{force:true})}return {ok:true};
  });locks.set(id,task);try{return await task}finally{if(locks.get(id)===task)locks.delete(id)}
 });
}
