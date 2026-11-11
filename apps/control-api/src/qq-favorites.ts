import {withFavoriteIndex} from '../../../packages/shared/qq-favorite-index.js';
import type {FastifyInstance} from 'fastify';import {readFile,writeFile,mkdir,rename,rm,readdir,lstat} from 'node:fs/promises';import path from 'node:path';import {createHash,randomUUID} from 'node:crypto';import {z} from 'zod';
import type {FavoriteAnnotation} from '../../../packages/shared/qq-favorite-semantics.js';
type Bot={id:string;protocol:string;qq_uin?:string;generation:number;desired_state:string;health?:any};
type Item={id:string;file:string;mime:string;meaning:string;avoid:string;enabled:boolean;animated:boolean;manual?:boolean;semantic?:FavoriteAnnotation;semanticStatus?:'ready'|'uncertain'|'pending'|'failed'|'raw_text';rawText?:string;rawSource?:string;semanticAttempts?:number;semanticFailures?:number;semanticRetryAt?:number;contentHash?:string;lastVisionError?:string;semanticWaitReason?:string;semanticWaitMs?:number;queuedAt?:number};
type Index={account:string;enabled:boolean;syncedAt?:string;items:Item[];failed?:number;limitReached?:boolean};
const invalid=(message:string,statusCode=400)=>Object.assign(new Error(message),{statusCode});
const hash=(raw:string)=>createHash('sha256').update(raw).digest('hex');
const safeFile=(file:string)=>/^qqf_[a-f0-9]{16}\.(gif|png)$/.test(file);
export {downloadFavorite} from '../../../packages/shared/qq-favorite-media.js';
import {downloadFavorite,nativeCall,normalize} from '../../../packages/shared/qq-favorite-media.js';
type Dependencies={call?:typeof nativeCall;download?:typeof downloadFavorite;normalize?:typeof normalize;caption?:(bot:Bot,file:string,signal:AbortSignal)=>Promise<FavoriteAnnotation>};
export function registerQQFavorites(app:FastifyInstance,root:string,owned:(req:any,id:string)=>Promise<Bot>,deps:Dependencies={}){
 const tasks=new Map<string,{controller:AbortController;promise:Promise<void>}>(),locks=new Map<string,Promise<unknown>>();let closing=false;
 const dir=(id:string)=>path.join(root,'bots',id,'qq-favorites');const indexFile=(id:string)=>path.join(dir(id),'index.json');
 async function atomic(file:string,data:unknown){await mkdir(path.dirname(file),{recursive:true});const temp=file+'.'+randomUUID()+'.tmp';try{await writeFile(temp,JSON.stringify(data),{flag:'wx',mode:0o600});for(let attempt=0;;attempt++){try{await rename(temp,file);break}catch(e:any){if(process.platform!=='win32'||!['EPERM','EACCES','EBUSY'].includes(e.code)||attempt>=4)throw e;await new Promise(r=>setTimeout(r,25*(attempt+1)))}}}finally{await rm(temp,{force:true})}}
 async function read(id:string){try{return await readFile(indexFile(id),'utf8')}catch(e:any){if(e.code==='ENOENT')return JSON.stringify({account:'',enabled:false,items:[]});throw e}}
 async function locked<T>(id:string,run:()=>Promise<T>){const before=locks.get(id)||Promise.resolve(),task=before.catch(()=>{}).then(()=>withFavoriteIndex(root,id,run));locks.set(id,task);try{return await task}finally{if(locks.get(id)===task)locks.delete(id)}}
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
   const before:Index=JSON.parse(await read(bot.id)),old=new Map((before.account===account(bot)?before.items:[]).map(i=>[i.id,i])),items:Item[]=[],staged=new Set<string>();let annotationAttempts=0;
   for(const source of selected){await active(req,bot,signal);const fingerprint=String(source.md5||source.resId||source.url||'');if(!fingerprint){failed++;continue}const id='qqf_'+hash(fingerprint).slice(0,16);if(items.some(i=>i.id===id))continue;const existing=old.get(id);
    try{let cached=false;let item:Item;if(existing&&safeFile(existing.file))try{const stat=await lstat(path.join(dir(bot.id),'images',existing.file));cached=stat.isFile()&&!stat.isSymbolicLink()&&stat.size<=1048576}catch{}if(cached){item={...existing!}}else{
     const bytes=await download(String(source.url||''),signal),input=path.join(stage,id+'.input');await writeFile(input,bytes,{mode:0o600});const output=await convert(input,path.join(stage,id),signal);await rm(input,{force:true});
     const meaning=String(source.modifyWord||source.desc||source.ocrWord||'').replace(/[\x00-\x1f\x7f]/g,' ').trim().slice(0,100);
     staged.add(id);item={id,file:id+output.suffix,mime:output.mime,animated:output.animated,rawText:meaning,rawSource:meaning?'qq_text':'',meaning:'',avoid:'',enabled:false,semanticStatus:'raw_text',semanticAttempts:0,semanticFailures:0};}
     // Bounded work per import, not a daily quota. Re-sync continues remaining
     // missing descriptions using cached images without another QQ download.
     if(item.meaning&&!item.manual&&!item.semantic){item.rawText=item.rawText||item.meaning;item.rawSource=item.rawSource||'legacy_text';item.meaning='';item.avoid='';item.enabled=false;item.semanticStatus='raw_text'}
     if(!item.meaning&&!item.manual&&item.semantic?.confidence!=='clear'&&deps.caption){
      if(annotationAttempts<8&&Number(item.semanticAttempts||0)<3&&Number(item.semanticFailures||0)<5&&Number(item.semanticRetryAt||0)<=Date.now()){
       item.semanticStatus='pending';
       annotationAttempts++;
       try{
        const semantic=await deps.caption(bot,path.join(cached?path.join(dir(bot.id),'images'):stage,item.file),signal);
        await active(req,bot,signal);item.semantic=semantic;
        item.semanticStatus=semantic.confidence==='clear'?'ready':'uncertain';
        if(semantic.confidence==='clear'){item.meaning=semantic.meaning;item.avoid=semantic.avoid;item.enabled=true}
        else{item.semanticAttempts=Number(item.semanticAttempts||0)+1;item.semanticRetryAt=Date.now()+60000*2**item.semanticAttempts}
       }catch(e){if(signal.aborted)throw e;const message=String((e as Error)?.message||e);if(/pressure|chat_busy/.test(message)){item.semanticWaitReason=/chat_busy/.test(message)?'chat_busy':'pressure';item.semanticRetryAt=Date.now()+60000}else{item.semanticStatus='failed';item.semanticFailures=Number(item.semanticFailures||0)+1;item.semanticRetryAt=Date.now()+Math.min(86400000,60000*2**item.semanticFailures)}}
      }
     }
     items.push(item);
    }catch(e){if(signal.aborted)throw e;failed++}processed++;await atomic(path.join(dir(bot.id),'sync.json'),{syncing:true,processed,total:selected.length});
   }
   if(selected.length&&items.length===0)throw invalid('收藏图片未能导入，请检查网络或图片格式');
   const finalIdentity=await call(root,bot,'get_login_info',{},signal);if(String(finalIdentity.user_id)!==account(bot))throw invalid('同步期间QQ账号已变化');
   await locked(bot.id,async()=>{await active(req,bot,signal);const current:Index=JSON.parse(await read(bot.id)),overrides=new Map((current.account===account(bot)?current.items:[]).map(i=>[i.id,i]));await mkdir(path.join(dir(bot.id),'images'),{recursive:true});
    for(const item of items){
     const override=overrides.get(item.id),initial=old.get(item.id);
     // Preserve edits made while annotation was in flight, but do not replace
     // a new generated meaning with its unchanged empty pre-import snapshot.
     if(override&&(!initial||JSON.stringify(override)!==JSON.stringify(initial))){for(const key of Object.keys(item))if(!(key in override))delete (item as any)[key];Object.assign(item,override);staged.delete(item.id)}
     if(staged.has(item.id))await rename(path.join(stage,item.file),path.join(dir(bot.id),'images',item.file));
    }
    await active(req,bot,signal);await atomic(indexFile(bot.id),{account:account(bot),enabled:current.account===account(bot)?current.enabled:false,items,syncedAt:new Date().toISOString(),failed,limitReached:list.length>=200});
    const keep=new Set(items.map(i=>i.file));for(const name of await readdir(path.join(dir(bot.id),'images')))if(safeFile(name)&&!keep.has(name))await rm(path.join(dir(bot.id),'images',name),{force:true});
   });await atomic(path.join(dir(bot.id),'sync.json'),{syncing:false,processed,total:selected.length});
  }catch(e:any){app.log.warn({botId:bot.id,code:typeof e?.code==='string'?e.code:'sync_failed'},'QQ favorite sync failed');if(!signal.aborted)await atomic(path.join(dir(bot.id),'sync.json'),{syncing:false,processed,error:'同步未完成，原收藏库保留。请确认QQ在线后重试。'}).catch(()=>{})}
  finally{const target=path.resolve(stage),parent=path.resolve(dir(bot.id))+path.sep;if(target.startsWith(parent))await rm(target,{recursive:true,force:true}).catch(()=>{});clearTimeout(deadline);tasks.delete(bot.id);resolve()}})();
  return reply.code(202).send({accepted:true});
 });
 const patch=z.object({expectedVersion:z.string().length(64),enabled:z.boolean().optional(),meaning:z.string().trim().max(100).optional(),avoid:z.string().max(100).optional()});
 app.patch<{Params:{id:string;image:string}}>('/v1/bots/:id/qq-favorites/:image',async req=>{await owned(req,req.params.id);const value=patch.parse(req.body);return locked(req.params.id,async()=>{const bot=await owned(req,req.params.id),raw=await read(bot.id),index:Index=JSON.parse(raw);if(hash(raw)!==value.expectedVersion)throw invalid('收藏已更新，请刷新后重试',409);if(!account(bot)||index.account!==account(bot))throw invalid('请先同步当前QQ的收藏',409);
  if(req.params.image==='settings'){if(value.enabled===undefined)throw invalid('缺少开关');index.enabled=value.enabled}else{const item=index.items.find(i=>i.id===req.params.image);if(!item)throw invalid('收藏表情不存在',404);if(value.meaning!==undefined)item.meaning=value.meaning;if(value.avoid!==undefined)item.avoid=value.avoid;if(value.enabled!==undefined)item.enabled=value.enabled;if(item.enabled&&!item.meaning)throw invalid('请先填写使用场景再启用');item.manual=true;delete item.semantic;delete item.semanticStatus}
  await atomic(indexFile(bot.id),index);return{ok:true}})});
 app.get<{Params:{id:string;image:string}}>('/v1/bots/:id/qq-favorites/:image',async(req,reply)=>{const bot=await owned(req,req.params.id),index:Index=JSON.parse(await read(bot.id));const item=index.account===account(bot)&&index.items.find(i=>i.id===req.params.image);if(!item||!safeFile(item.file))throw invalid('收藏表情不存在',404);let bytes:Buffer;try{bytes=await readFile(path.join(dir(bot.id),'images',item.file))}catch{throw invalid('收藏表情不存在',404)}if(bytes.length>1048576)throw invalid('图片尺寸异常');return reply.header('Cache-Control','private, no-store').type(item.mime).send(bytes)});
}
