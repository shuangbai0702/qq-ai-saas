import {SingleFlight} from '../../../packages/shared/single-flight.js';
/** Bounded maintenance on the existing Agent tick, no per-bot timer/Agent.
 * Semantics (D8): QQ raw text (modifyWord/desc/ocrWord) is stored as rawText
 * and never enables automatic selection. A verified vision result publishes
 * either a known use or an objective description, retaining uncertainty. */
import {mkdir,writeFile,rm,rename,lstat,readdir,readFile} from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';
import {createFavoriteCaptioner,favoriteAnnotationDue,applyFavoriteAnnotation,promoteFavoriteDescription,favoriteSelectionText} from '../../../packages/shared/qq-favorite-semantics.js';
import {downloadFavorite,nativeCall,normalize} from '../../../packages/shared/qq-favorite-media.js';
import {favoriteDirectory,readFavoriteIndex,writeFavoriteIndex,withFavoriteIndex} from '../../../packages/shared/qq-favorite-index.js';
const MAX_QUEUE_TICKS=2,MAX_ACTIVE_BOTS=3;
async function contentHash(file:string){const bytes=await readFile(file);return createHash('sha256').update(bytes).digest('hex')}
export class FavoriteMaintenance{
 closed=false;private flight=new SingleFlight();private active=new Map<string,Promise<void>>();
 get job(){const jobs=[this.flight.pending,...this.active.values()].filter((p):p is Promise<void>=>Boolean(p));return jobs.length?Promise.all(jobs).then(()=>{}):undefined}
 controller=new AbortController();next=0;cursor='';private nextDiscovery=new Map<string,number>();
 caption:ReturnType<typeof createFavoriteCaptioner>;
 constructor(public db:Sql,public root:string,key:Buffer,public deps={call:nativeCall,download:downloadFavorite,convert:normalize}){this.caption=createFavoriteCaptioner(db,key,root)}
 tick(){if(this.closed||this.flight.pending||Date.now()<this.next)return;this.next=Date.now()+5000;if(this.controller.signal.aborted)this.controller=new AbortController();const signal=this.controller.signal;void this.flight.run(()=>this.run(signal).catch(()=>{/* retries persisted; no protocol restart */}))}
 async stop(){this.closed=true;this.controller?.abort();await this.job}
 async valid(bot:any,signal:AbortSignal){signal.throwIfAborted();const [current]=await this.db.query('SELECT b.*,q.qq_uin FROM bots b JOIN qq_bindings q ON q.bot_id=b.id WHERE b.id=$1 AND b.tenant_id=$2 AND b.deleted_at IS NULL',[bot.id,bot.tenant_id]);if(!current||current.generation!==bot.generation||current.revision!==bot.revision||current.qq_uin!==bot.qq_uin||!runtimeStatus(current).online)throw new Error('favorite_owner_changed')}
 async run(signal:AbortSignal){
  const bots=await this.db.query("SELECT b.*,q.qq_uin,c.config FROM bots b JOIN qq_bindings q ON q.bot_id=b.id JOIN bot_config_revisions c ON c.bot_id=b.id AND c.revision=b.revision WHERE b.deleted_at IS NULL AND b.desired_state='running' AND b.protocol='napcat' ORDER BY b.id");
  const eligible=bots.filter(b=>runtimeStatus(b).online&&b.config?.behavior?.memeEnabled&&b.config?.behavior?.qqFavoriteSaveEnabled);
  const present=new Set(eligible.map(b=>b.id));for(const id of this.nextDiscovery.keys())if(!present.has(id))this.nextDiscovery.delete(id);
  const ordered=[...eligible.filter(b=>b.id>this.cursor),...eligible.filter(b=>b.id<=this.cursor)];
  // The existing saved originals are a wake hint, never proof of QQ success.
  // Inspect only small local metadata; idle accounts retain minute discovery.
  const candidates=[];
  for(const bot of ordered){
   signal.throwIfAborted();
   if(this.active.has(bot.id))continue;
   try{candidates.push({bot,fresh:await this.recentSavePending(bot)})}
   catch(error){
    signal.throwIfAborted();
    if(Date.now()>=(this.nextDiscovery.get(bot.id)||0)){
     this.nextDiscovery.set(bot.id,Date.now()+60000);
     console.warn('favorite candidate unavailable',{botId:bot.id,generation:bot.generation,error:error instanceof Error?error.name:'UnknownError'});
    }
   }
  }
  candidates.sort((a,b)=>Number(b.fresh)-Number(a.fresh));
  for(const {bot,fresh} of candidates){
   signal.throwIfAborted();if(this.closed||this.active.size>=MAX_ACTIVE_BOTS)break;
   if(!fresh&&Date.now()<(this.nextDiscovery.get(bot.id)||0))continue;
   this.cursor=bot.id;this.nextDiscovery.set(bot.id,Date.now()+60000);
   const taskSignal=AbortSignal.any([signal,AbortSignal.timeout(120000)]);
   // The Agent's existing tick owns scheduling; a slow account cannot hold
   // the scheduler. No per-Bot timer, durable queue or duplicate index.
   const task=Promise.resolve().then(()=>this.maintain(bot,taskSignal)).catch(()=>{/* index retains retry state */}).finally(()=>{if(this.active.get(bot.id)===task)this.active.delete(bot.id)});
   this.active.set(bot.id,task);
  }
 }
 async recentSavePending(bot:any){
  const directory=path.join(favoriteDirectory(this.root,bot.id),'uploads');
  const folder=await lstat(directory).catch(error=>{if((error as NodeJS.ErrnoException).code==='ENOENT')return null;throw error});
  if(!folder?.isDirectory()||folder.isSymbolicLink()||Date.now()-folder.mtimeMs>300000)return false;
  let files:string[];try{files=await readdir(directory)}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return false;throw error}
  const index=await readFavoriteIndex(this.root,bot.id);if(index.account&&index.account!==String(bot.qq_uin))return false;
  for(const name of files.filter(name=>/^[a-f0-9]{32}\.image$/.test(name)).slice(0,200)){
   const info=await lstat(path.join(directory,name)).catch(()=>null);
   if(!info?.isFile()||info.isSymbolicLink()||Date.now()-info.mtimeMs>300000)continue;
   const id='qqf_'+createHash('sha256').update(name.slice(0,-6)).digest('hex').slice(0,16);
   const item=index.items.find((item:any)=>item.id===id);
   if(!item||favoriteAnnotationDue(item))return true;
  }
  return false;
 }
 // Old-layout items whose meaning came from QQ raw text lose the automatic
 // enablement but keep their picture and record. Stored vision descriptions
 // become selectable without another model request; manual items are untouched.
 async reconcileAnnotations(bot:any,signal:AbortSignal){
  const before=await readFavoriteIndex(this.root,bot.id);if(before.account!==String(bot.qq_uin))return;
  if(!before.items.some((i:any)=>!i.manual&&(i.meaning&&!i.semantic||!i.meaning&&['uncertain','failed'].includes(i.semanticStatus)&&favoriteSelectionText(i.semantic))))return;
  await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const index=await readFavoriteIndex(this.root,bot.id);if(index.account!==String(bot.qq_uin))return;
   let changed=false;
   for(const item of index.items){
    if(promoteFavoriteDescription(item))changed=true;
    if(item.meaning&&!item.manual&&!item.semantic){item.rawText=item.rawText||String(item.meaning);item.rawSource=item.rawSource||'legacy_text';item.meaning='';item.avoid='';item.enabled=false;item.semanticStatus='raw_text';changed=true}
   }
   if(changed)await writeFavoriteIndex(this.root,bot.id,index);
  });
 }
 async maintain(bot:any,signal:AbortSignal){
  await this.valid(bot,signal);const directory=favoriteDirectory(this.root,bot.id),images=path.join(directory,'images');await mkdir(images,{recursive:true});
  for(const entry of (await readdir(directory)).filter(n=>/^stage-[a-f0-9-]{36}$/.test(n)).slice(0,20)){
   const target=path.join(directory,entry),stat=await lstat(target);if(stat.isDirectory()&&!stat.isSymbolicLink()&&Date.now()-stat.mtimeMs>86400000)await rm(target,{recursive:true,force:true});
  }
  const identity=await this.deps.call(this.root,bot,'get_login_info',{},signal);if(String(identity.user_id)!==String(bot.qq_uin))throw new Error('favorite_identity_mismatch');
  let raw:any;
  try{raw=await this.deps.call(this.root,bot,'fetch_custom_face_detail',{count:200},signal)}catch(error){
   // Discovery is not the authority for already cached, account-owned images.
   // An unavailable QQ catalog must not starve their independent annotation.
   await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const index=await readFavoriteIndex(this.root,bot.id);
    if(index.account&&index.account!==String(bot.qq_uin))throw new Error('favorite_account_changed');
    index.lastDiscoveryFailure={at:Date.now(),error:String((error as Error)?.message||error).slice(0,120)};
    await writeFavoriteIndex(this.root,bot.id,index);
   });
  }
  signal.throwIfAborted();
  const sources=Array.isArray(raw)?raw:Array.isArray(raw?.data)?raw.data:[];
  const saved:string[]=await readdir(path.join(directory,'uploads')).catch(error=>{if((error as NodeJS.ErrnoException).code==='ENOENT')return [];throw error});
  const savedIds=new Set(saved.filter(name=>/^[a-f0-9]{32}\.image$/.test(name)).slice(0,200).map(name=>'qqf_'+createHash('sha256').update(name.slice(0,-6)).digest('hex').slice(0,16)));
  sources.sort((a:any,b:any)=>Number(saved.includes(String(b.md5||'')+'.image'))-Number(saved.includes(String(a.md5||'')+'.image')));
  // A bounded batch must advance past failures, otherwise four bad originals
  // permanently hide every later favorite. Cursor lives in the same index.
  const importIndex=await readFavoriteIndex(this.root,bot.id);
  const cursorAt=sources.findIndex((s:any)=>'qqf_'+createHash('sha256').update(String(s.md5||s.resId||s.url||'')).digest('hex').slice(0,16)===importIndex.importCursor);
  if(cursorAt>=0)sources.push(...sources.splice(0,cursorAt+1));
  let attempted=0,lastAttempted='';const importedIds=new Set<string>();
  for(const source of sources.slice(0,200)){
   signal.throwIfAborted();if(attempted>=4)break;const fingerprint=String(source.md5||source.resId||source.url||'');if(!fingerprint)continue;
   const id='qqf_'+createHash('sha256').update(fingerprint).digest('hex').slice(0,16);
   const existing=await readFavoriteIndex(this.root,bot.id);if(existing.account===String(bot.qq_uin)&&existing.items.some((i:any)=>i.id===id))continue;
   attempted++;lastAttempted=id;
   const stage=path.join(directory,'stage-'+randomUUID());await mkdir(stage,{recursive:true});
   let mediaReady=false;try{
    const input=path.join(stage,id+'.input');await writeFile(input,await this.deps.download(String(source.url||''),signal),{mode:0o600});const result=await this.deps.convert(input,path.join(stage,id),signal);
    mediaReady=true;
    await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const index=await readFavoriteIndex(this.root,bot.id);
     if(index.account&&index.account!==String(bot.qq_uin))throw new Error('favorite_account_changed');
     if(index.items.some((i:any)=>i.id===id)||index.items.length>=200)return;
     // Raw QQ text is evidence about the source, never a ready meaning.
     const rawText=String(source.modifyWord||source.desc||source.ocrWord||'').replace(/[\x00-\x1f\x7f]/g,' ').trim().slice(0,100),file=id+result.suffix;
     await rename(path.join(stage,file),path.join(images,file));
     index.account=String(bot.qq_uin);index.items.push({id,file,mime:result.mime,animated:result.animated,rawText,rawSource:rawText?'qq_text':'',meaning:'',avoid:'',enabled:false,semanticStatus:'raw_text',semanticAttempts:0,semanticFailures:0,semanticRetryAt:0});
     // Existing explicit selection preference is never changed by discovery.
     await writeFavoriteIndex(this.root,bot.id,index);importedIds.add(id);
    });
   }catch(error){
    if(mediaReady||signal.aborted||['ENOSPC','EACCES','EPERM','EROFS'].includes(String((error as NodeJS.ErrnoException)?.code)))throw error;
    // A single unsupported original must not starve already cached images.
    // Identity/cancellation are checked again before recording any failure.
    await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const index=await readFavoriteIndex(this.root,bot.id);
     if(index.account&&index.account!==String(bot.qq_uin))throw new Error('favorite_account_changed');
     index.lastImportFailure={id,at:Date.now(),error:String((error as Error)?.message||error).slice(0,120)};
     await writeFavoriteIndex(this.root,bot.id,index);
    });
   }finally{await rm(stage,{recursive:true,force:true})}
  }
  if(attempted)await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const index=await readFavoriteIndex(this.root,bot.id);
   if(index.account&&index.account!==String(bot.qq_uin))throw new Error('favorite_account_changed');
   if(attempted===4)index.importCursor=lastAttempted;else delete index.importCursor;
   await writeFavoriteIndex(this.root,bot.id,index);
  });
  await this.reconcileAnnotations(bot,signal);
  const index=await readFavoriteIndex(this.root,bot.id);if(index.account!==String(bot.qq_uin))return;
  const due=index.items.filter((i:any)=>favoriteAnnotationDue(i)).sort((a:any,b:any)=>Number(importedIds.has(b.id))-Number(importedIds.has(a.id))||Number(savedIds.has(b.id))-Number(savedIds.has(a.id)));
  let attemptedAnnotations=0,inspected=0;
  for(const item of due){
   signal.throwIfAborted();if(attemptedAnnotations>=MAX_QUEUE_TICKS)break;inspected++;
   if(typeof item.file!=='string'||!/^qqf_[a-f0-9]{16}\.(gif|png)$/.test(item.file))continue;
   const file=path.join(images,item.file);
   const info=await lstat(file).catch(error=>{if((error as NodeJS.ErrnoException).code==='ENOENT')return null;throw error});
   if(!info||!info.isFile()||info.isSymbolicLink()||info.size>1048576)continue;
   attemptedAnnotations++;
   const startedAt=Date.now(),before=await contentHash(file).catch(()=>null);
   let result:any,error:unknown;try{result=await this.caption(bot,file,signal)}catch(e){if(signal.aborted)throw e;error=e}
   const after=await contentHash(file).catch(()=>null);
   await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const latest=await readFavoriteIndex(this.root,bot.id);if(latest.account!==String(bot.qq_uin))return;
    const current=latest.items.find((i:any)=>i.id===item.id);if(!current||current.manual||current.meaning||current.file!==item.file)return;
    if(!error&&(before!==after||!before)){current.lastVisionError='favorite_content_changed';current.semanticRetryAt=Date.now()+60000}
    else{applyFavoriteAnnotation(current,result,error,Date.now(),Math.max(0,Date.now()-startedAt));if(!error)current.contentHash=after}
    await writeFavoriteIndex(this.root,bot.id,latest);
   });
  }
  if(due.length>inspected)this.nextDiscovery.set(bot.id,Date.now()+5000);
 }
}
/** Bounded stall diagnosis for the d18 queue: evidence only, no gate changes. */
export async function favoriteQueueDiagnostics(db:Sql,root:string,botId:string){
 const [bot]=await db.query('SELECT b.*,q.qq_uin,c.config FROM bots b JOIN qq_bindings q ON q.bot_id=b.id JOIN bot_config_revisions c ON c.bot_id=b.id AND c.revision=b.revision WHERE b.id=$1 AND b.deleted_at IS NULL',[botId]);
 if(!bot)return null;
 const index=await readFavoriteIndex(root,botId),accountMatched=index.account===String(bot.qq_uin),images=path.join(favoriteDirectory(root,botId),'images');
 const items=(accountMatched?index.items:[]).map((i:any)=>({id:i.id,manual:Boolean(i.manual),meaning:Boolean(i.meaning),enabled:Boolean(i.enabled),semanticStatus:i.semanticStatus||null,uncertainAttempts:Number(i.semanticAttempts||0),failures:Number(i.semanticFailures||0),waitReason:i.semanticWaitReason||null,waitMs:Number(i.semanticWaitMs||0),retryAt:Number(i.semanticRetryAt||0)||null,queuedAt:Number(i.queuedAt||0)||null,lastVisionError:i.lastVisionError||null,fileReadable:false}));
 for(const item of items){
  for(const suffix of ['.gif','.png']){
   const info=await lstat(path.join(images,String(item.id)+suffix)).catch(()=>null);
   if(info&&info.isFile()&&!info.isSymbolicLink()){item.fileReadable=true;break}
  }
 }
 const queued=items.filter((i:{manual:boolean;meaning:boolean;semanticStatus:string|null})=>!i.manual&&!i.meaning&&i.semanticStatus!=='ready');
 const oldestQueuedAt=Math.min(...queued.map((i:{queuedAt:number|null})=>Number(i.queuedAt)).filter((at:number)=>Number.isFinite(at)&&at>0));
 return{botId,account:String(bot.qq_uin||''),indexAccount:index.account,accountMatched,revision:bot.revision,providerReady:Boolean(runtimeStatus(bot).online&&bot.config?.baseUrl&&bot.config?.model),memeEnabled:Boolean(bot.config?.behavior?.memeEnabled),qqFavoriteSaveEnabled:Boolean(bot.config?.behavior?.qqFavoriteSaveEnabled),indexEnabled:Boolean(index.enabled),queueSize:queued.length,oldestQueuedAt:Number.isFinite(oldestQueuedAt)?oldestQueuedAt:null,items};
}
