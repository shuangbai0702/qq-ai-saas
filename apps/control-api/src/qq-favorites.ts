import type {FastifyInstance} from 'fastify';import {readFile,writeFile,mkdir,rename,rm,readdir,lstat} from 'node:fs/promises';import path from 'node:path';import {createHash,randomUUID} from 'node:crypto';import {execFile} from 'node:child_process';import {z} from 'zod';
type Bot={id:string;protocol:string;qq_uin?:string;generation:number;desired_state:string;health?:any};
type Item={id:string;file:string;mime:string;meaning:string;avoid:string;enabled:boolean;animated:boolean};
type Index={account:string;enabled:boolean;syncedAt?:string;items:Item[];failed?:number;limitReached?:boolean};
const invalid=(message:string,statusCode=400)=>Object.assign(new Error(message),{statusCode});
const hash=(raw:string)=>createHash('sha256').update(raw).digest('hex');
const safeFile=(file:string)=>/^qqf_[a-f0-9]{16}\.(gif|png)$/.test(file);
export async function downloadFavorite(url:string,signal:AbortSignal){
 const u=new URL(url),host=u.hostname.toLowerCase();if(u.protocol!=='https:'||u.username||u.password||(u.port&&u.port!=='443')||!['qpic.cn','qq.com'].some(domain=>host===domain||host.endsWith('.'+domain)))throw invalid('收藏图片来源不受支持');
 const response=await fetch(u,{signal:AbortSignal.any([signal,AbortSignal.timeout(15000)]),redirect:'error'});if(!response.ok||!response.body||Number(response.headers.get('content-length')||0)>8388608)throw invalid('收藏图片暂时不可用');
 const blocks:Uint8Array[]=[];let length=0;try{for await(const block of response.body){length+=block.length;if(length>8388608)throw invalid('收藏图片超过8MiB');blocks.push(block)}}catch(e){await response.body.cancel().catch(()=>{});throw e}return Buffer.concat(blocks);
}
async function nativeCall(root:string,bot:Bot,action:string,params:unknown,signal:AbortSignal){
 if(bot.protocol!=='napcat')throw invalid('当前协议端尚不支持QQ收藏同步',409);
 const m=JSON.parse(await readFile(path.join(root,'bots',bot.id,'napcat-platform.json'),'utf8'));if(m.botId!==bot.id||m.management.host!=='127.0.0.1'||!Number.isInteger(m.management.port))throw invalid('协议实例尚未就绪',409);
 const response=await fetch(`http://127.0.0.1:${m.management.port+1}/${action}`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+m.reverseWs.token},body:JSON.stringify(params),signal:AbortSignal.any([signal,AbortSignal.timeout(12000)])});
 const result:any=await response.json();if(!response.ok||result.status!=='ok'||result.retcode!==0)throw invalid('QQ收藏接口暂时不可用，请确认QQ在线',409);return result.data;
}
async function normalize(input:string,destination:string,signal:AbortSignal){
 const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 return new Promise<{suffix:string;mime:string;animated:boolean}>((resolve,reject)=>execFile(python,[path.resolve('adapters/astrbot/favorite_media.py'),input,destination],{signal,timeout:15000,maxBuffer:8192,windowsHide:true,env:{PATH:process.env.PATH,SystemRoot:process.env.SystemRoot,TEMP:process.env.TEMP,TMP:process.env.TMP,PYTHONIOENCODING:'utf-8'}},(error,out)=>{if(error)return reject(invalid('收藏图片格式或尺寸不受支持'));try{resolve(JSON.parse(out))}catch{reject(invalid('收藏图片处理失败'))}}));
}
type Dependencies={call?:typeof nativeCall;download?:typeof downloadFavorite;normalize?:typeof normalize};
export function registerQQFavorites(app:FastifyInstance,root:string,owned:(req:any,id:string)=>Promise<Bot>,deps:Dependencies={}){
 const tasks=new Map<string,{controller:AbortController;promise:Promise<void>}>(),locks=new Map<string,Promise<unknown>>();let closing=false;
 const dir=(id:string)=>path.join(root,'bots',id,'qq-favorites');const indexFile=(id:string)=>path.join(dir(id),'index.json');
 async function atomic(file:string,data:unknown){await mkdir(path.dirname(file),{recursive:true});const temp=file+'.'+randomUUID()+'.tmp';try{await writeFile(temp,JSON.stringify(data),{flag:'wx',mode:0o600});await rename(temp,file)}finally{await rm(temp,{force:true})}}
 async function read(id:string){try{return await readFile(indexFile(id),'utf8')}catch(e:any){if(e.code==='ENOENT')return JSON.stringify({account:'',enabled:false,items:[]});throw e}}
 async function locked<T>(id:string,run:()=>Promise<T>){const before=locks.get(id)||Promise.resolve(),task=before.catch(()=>{}).then(run);locks.set(id,task);try{return await task}finally{if(locks.get(id)===task)locks.delete(id)}}
 function account(bot:Bot){return String(bot.qq_uin||'')}
 async function active(req:any,initial:Bot,signal:AbortSignal){signal.throwIfAborted();const b=await owned(req,initial.id);if(b.generation!==initial.generation||account(b)!==account(initial)||b.desired_state==='deleting')throw invalid('QQ账号或实例已切换，请重新同步',409);return b}
 async function state(id:string){try{const s=JSON.parse(await readFile(path.join(dir(id),'sync.json'),'utf8'));return s.syncing&&!tasks.has(id)?{...s,syncing:false,error:'上次同步已中断，请重新同步'}:s}catch(e:any){if(e.code==='ENOENT')return{syncing:false};throw e}}
 const call=deps.call||nativeCall,download=deps.download||downloadFavorite,convert=deps.normalize||normalize;
 app.addHook('onClose',async()=>{closing=true;for(const task of tasks.values())task.controller.abort();await Promise.allSettled([...tasks.values()].map(t=>t.promise))});
 app.get<{Params:{id:string}}>('/v1/bots/:id/qq-favorites',async req=>{const bot=await owned(req,req.params.id),raw=await read(bot.id),data:Index=JSON.parse(raw),matches=account(bot)&&data.account===account(bot);return{supported:bot.protocol==='napcat',enabled:matches?data.enabled:false,items:matches?data.items.map(({file,...item})=>({...item,hasMeaning:!!item.meaning})):[],version:hash(raw),syncedAt:matches?data.syncedAt:null,failed:matches?data.failed:0,limitReached:matches?data.limitReached:false,limit:200,...await state(bot.id)}});
 app.post<{Params:{id:string}}>('/v1/bots/:id/qq-favorites/sync',{config:{rateLimit:{max:3,timeWindow:'1 minute'}}},async(req,reply)=>{
  const bot=await owned(req,req.params.id);if(closing||tasks.size>=2)throw invalid('同步任务较多，请稍后重试',429);if(tasks.has(bot.id))throw invalid('此机器人正在同步，请等待完成',409);if(bot.protocol!=='napcat'||!account(bot)||!bot.health?.protocol?.online)throw invalid('请先让NapCat QQ真实在线，再同步收藏',409);
  const controller=new AbortController();const deadline=setTimeout(()=>controller.abort(),300000);deadline.unref();const signal=controller.signal;let resolve!:()=>void;const promise=new Promise<void>(r=>resolve=r);tasks.set(bot.id,{controller,promise});
  try{await atomic(path.join(dir(bot.id),'sync.json'),{syncing:true,processed:0,total:0})}catch(e){clearTimeout(deadline);tasks.delete(bot.id);resolve();throw e}
  void(async()=>{let processed=0,failed=0;const stage=path.join(dir(bot.id),'stage-'+randomUUID());try{
   const identity=await call(root,bot,'get_login_info',{},signal);if(String(identity.user_id)!==account(bot))throw invalid('QQ在线身份与绑定不一致');
   const list=await call(root,bot,'fetch_custom_face_detail',{count:200},signal);if(!Array.isArray(list))throw invalid('QQ收藏接口返回异常');const selected=list.slice(0,200);await mkdir(stage,{recursive:true});
   const before:Index=JSON.parse(await read(bot.id)),old=new Map((before.account===account(bot)?before.items:[]).map(i=>[i.id,i])),items:Item[]=[],staged=new Set<string>();
   for(const source of selected){await active(req,bot,signal);const fingerprint=String(source.md5||source.resId||source.url||'');if(!fingerprint){failed++;continue}const id='qqf_'+hash(fingerprint).slice(0,16);if(items.some(i=>i.id===id))continue;const existing=old.get(id);
    try{let cached=false;if(existing&&safeFile(existing.file))try{const stat=await lstat(path.join(dir(bot.id),'images',existing.file));cached=stat.isFile()&&!stat.isSymbolicLink()&&stat.size<=1048576}catch{}if(cached){items.push(existing!)}else{
     const bytes=await download(String(source.url||''),signal),input=path.join(stage,id+'.input');await writeFile(input,bytes,{mode:0o600});const output=await convert(input,path.join(stage,id),signal);await rm(input,{force:true});
     const meaning=String(source.modifyWord||source.desc||source.ocrWord||'').replace(/[\x00-\x1f\x7f]/g,' ').trim().slice(0,100);
     staged.add(id);items.push({id,file:id+output.suffix,mime:output.mime,animated:output.animated,meaning,avoid:'',enabled:!!meaning});}
    }catch(e){if(signal.aborted)throw e;failed++}processed++;await atomic(path.join(dir(bot.id),'sync.json'),{syncing:true,processed,total:selected.length});
   }
   if(selected.length&&items.length===0)throw invalid('收藏图片未能导入，请检查网络或图片格式');
   const finalIdentity=await call(root,bot,'get_login_info',{},signal);if(String(finalIdentity.user_id)!==account(bot))throw invalid('同步期间QQ账号已变化');
   await locked(bot.id,async()=>{await active(req,bot,signal);const current:Index=JSON.parse(await read(bot.id)),overrides=new Map((current.account===account(bot)?current.items:[]).map(i=>[i.id,i]));await mkdir(path.join(dir(bot.id),'images'),{recursive:true});
    for(const item of items){const override=overrides.get(item.id);if(override)Object.assign(item,{meaning:override.meaning,avoid:override.avoid,enabled:override.enabled});if(staged.has(item.id))await rename(path.join(stage,item.file),path.join(dir(bot.id),'images',item.file))}
    await active(req,bot,signal);await atomic(indexFile(bot.id),{account:account(bot),enabled:current.account===account(bot)?current.enabled:false,items,syncedAt:new Date().toISOString(),failed,limitReached:list.length>=200});
    const keep=new Set(items.map(i=>i.file));for(const name of await readdir(path.join(dir(bot.id),'images')))if(safeFile(name)&&!keep.has(name))await rm(path.join(dir(bot.id),'images',name),{force:true});
   });await atomic(path.join(dir(bot.id),'sync.json'),{syncing:false,processed,total:selected.length});
  }catch{if(!signal.aborted)await atomic(path.join(dir(bot.id),'sync.json'),{syncing:false,processed,error:'同步未完成，原收藏库保留。请确认QQ在线后重试。'}).catch(()=>{})}
  finally{const target=path.resolve(stage),parent=path.resolve(dir(bot.id))+path.sep;if(target.startsWith(parent))await rm(target,{recursive:true,force:true}).catch(()=>{});clearTimeout(deadline);tasks.delete(bot.id);resolve()}})();
  return reply.code(202).send({accepted:true});
 });
 const patch=z.object({expectedVersion:z.string().length(64),enabled:z.boolean().optional(),meaning:z.string().trim().max(100).optional(),avoid:z.string().max(100).optional()});
 app.patch<{Params:{id:string;image:string}}>('/v1/bots/:id/qq-favorites/:image',async req=>{await owned(req,req.params.id);const value=patch.parse(req.body);return locked(req.params.id,async()=>{const bot=await owned(req,req.params.id),raw=await read(bot.id),index:Index=JSON.parse(raw);if(hash(raw)!==value.expectedVersion)throw invalid('收藏已更新，请刷新后重试',409);if(!account(bot)||index.account!==account(bot))throw invalid('请先同步当前QQ的收藏',409);
  if(req.params.image==='settings'){if(value.enabled===undefined)throw invalid('缺少开关');index.enabled=value.enabled}else{const item=index.items.find(i=>i.id===req.params.image);if(!item)throw invalid('收藏表情不存在',404);if(value.meaning!==undefined)item.meaning=value.meaning;if(value.avoid!==undefined)item.avoid=value.avoid;if(value.enabled!==undefined)item.enabled=value.enabled;if(item.enabled&&!item.meaning)throw invalid('请先填写使用场景再启用')}
  await atomic(indexFile(bot.id),index);return{ok:true}})});
 app.get<{Params:{id:string;image:string}}>('/v1/bots/:id/qq-favorites/:image',async(req,reply)=>{const bot=await owned(req,req.params.id),index:Index=JSON.parse(await read(bot.id));const item=index.account===account(bot)&&index.items.find(i=>i.id===req.params.image);if(!item||!safeFile(item.file))throw invalid('收藏表情不存在',404);let bytes:Buffer;try{bytes=await readFile(path.join(dir(bot.id),'images',item.file))}catch{throw invalid('收藏表情不存在',404)}if(bytes.length>1048576)throw invalid('图片尺寸异常');return reply.header('Cache-Control','private, no-store').type(item.mime).send(bytes)});
}
