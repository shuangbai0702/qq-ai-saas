/** Bounded maintenance on the existing Agent tick, no per-bot timer/Agent.
 * Semantics (D8): QQ raw text (modifyWord/desc/ocrWord) is stored as rawText
 * and never enables automatic selection; only a manual entry or a vision
 * result with confidence=clear whose content hash still matches publishes a
 * meaning. Uncertain images get at most 3 real vision attempts; pressure or
 * chat-busy defers without consuming an attempt. */
import {mkdir,writeFile,rm,rename,lstat,readdir,stat,readFile} from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';
import {createFavoriteCaptioner} from '../../../packages/shared/qq-favorite-semantics.js';
import {downloadFavorite,nativeCall,normalize} from '../../../packages/shared/qq-favorite-media.js';
import {favoriteDirectory,readFavoriteIndex,writeFavoriteIndex,withFavoriteIndex} from '../../../packages/shared/qq-favorite-index.js';
const UNCERTAIN_ATTEMPT_LIMIT=3,FAILURE_ATTEMPT_LIMIT=5,MAX_QUEUE_TICKS=2;
const deferError=(error:unknown)=>/pressure|chat_busy/.test(String((error as Error)?.message||error));
async function contentHash(file:string){const bytes=await readFile(file);return createHash('sha256').update(bytes).digest('hex')}
export class FavoriteMaintenance{
 closed=false;job?:Promise<void>;controller?:AbortController;next=0;cursor='';
 caption:ReturnType<typeof createFavoriteCaptioner>;
 constructor(public db:Sql,public root:string,key:Buffer,public deps={call:nativeCall,download:downloadFavorite,convert:normalize}){this.caption=createFavoriteCaptioner(db,key,root)}
 tick(){if(this.closed||this.job||Date.now()<this.next)return;this.next=Date.now()+60000;this.controller=new AbortController();const signal=AbortSignal.any([this.controller.signal,AbortSignal.timeout(120000)]);this.job=this.run(signal).catch(()=>{/* retries persisted; no protocol restart */}).finally(()=>{this.job=undefined;this.controller=undefined})}
 async stop(){this.closed=true;this.controller?.abort();await this.job}
 async valid(bot:any,signal:AbortSignal){signal.throwIfAborted();const [current]=await this.db.query('SELECT b.*,q.qq_uin FROM bots b JOIN qq_bindings q ON q.bot_id=b.id WHERE b.id=$1 AND b.tenant_id=$2 AND b.deleted_at IS NULL',[bot.id,bot.tenant_id]);if(!current||current.generation!==bot.generation||current.revision!==bot.revision||current.qq_uin!==bot.qq_uin||!runtimeStatus(current).online)throw new Error('favorite_owner_changed')}
 async run(signal:AbortSignal){
  const bots=await this.db.query("SELECT b.*,q.qq_uin,c.config FROM bots b JOIN qq_bindings q ON q.bot_id=b.id JOIN bot_config_revisions c ON c.bot_id=b.id AND c.revision=b.revision WHERE b.deleted_at IS NULL AND b.desired_state='running' AND b.protocol='napcat' ORDER BY b.id");
  const eligible=bots.filter(b=>runtimeStatus(b).online&&b.config?.behavior?.memeEnabled&&b.config?.behavior?.qqFavoriteSaveEnabled);
  const bot=eligible.find(b=>b.id>this.cursor)||eligible[0];if(!bot)return;this.cursor=bot.id;
  await this.maintain(bot,signal);
 }
 // Old-layout items whose meaning came from QQ raw text lose the automatic
 // enablement but keep their picture and record. Valid vision-clear and
 // manual items are preserved untouched.
 async migrateLegacyRawText(bot:any,signal:AbortSignal){
  const before=await readFavoriteIndex(this.root,bot.id);if(before.account!==String(bot.qq_uin))return;
  if(!before.items.some((i:any)=>i.meaning&&!i.manual&&!i.semantic))return;
  await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const index=await readFavoriteIndex(this.root,bot.id);if(index.account!==String(bot.qq_uin))return;
   let changed=false;
   for(const item of index.items){
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
  const raw=await this.deps.call(this.root,bot,'fetch_custom_face_detail',{count:200},signal),sources=Array.isArray(raw)?raw:Array.isArray(raw?.data)?raw.data:[];
  let imported=0;
  for(const source of sources.slice(0,200)){
   signal.throwIfAborted();if(imported>=4)break;const fingerprint=String(source.md5||source.resId||source.url||'');if(!fingerprint)continue;
   const id='qqf_'+createHash('sha256').update(fingerprint).digest('hex').slice(0,16);
   const existing=await readFavoriteIndex(this.root,bot.id);if(existing.account===String(bot.qq_uin)&&existing.items.some((i:any)=>i.id===id))continue;
   const stage=path.join(directory,'stage-'+randomUUID());await mkdir(stage,{recursive:true});
   try{
    const input=path.join(stage,id+'.input');await writeFile(input,await this.deps.download(String(source.url||''),signal),{mode:0o600});const result=await this.deps.convert(input,path.join(stage,id),signal);
    await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const index=await readFavoriteIndex(this.root,bot.id);
     if(index.account&&index.account!==String(bot.qq_uin))throw new Error('favorite_account_changed');
     if(index.items.some((i:any)=>i.id===id)||index.items.length>=200)return;
     // Raw QQ text is evidence about the source, never a ready meaning.
     const rawText=String(source.modifyWord||source.desc||source.ocrWord||'').replace(/[\x00-\x1f\x7f]/g,' ').trim().slice(0,100),file=id+result.suffix;
     await rename(path.join(stage,file),path.join(images,file));
     index.account=String(bot.qq_uin);index.items.push({id,file,mime:result.mime,animated:result.animated,rawText,rawSource:rawText?'qq_text':'',meaning:'',avoid:'',enabled:false,semanticStatus:'raw_text',semanticAttempts:0,semanticFailures:0,semanticRetryAt:0});
     // Existing explicit selection preference is never changed by discovery.
     await writeFavoriteIndex(this.root,bot.id,index);imported++;
    });
   }finally{await rm(stage,{recursive:true,force:true})}
  }
  await this.migrateLegacyRawText(bot,signal);
  const index=await readFavoriteIndex(this.root,bot.id);if(index.account!==String(bot.qq_uin))return;
  const pending=index.items.filter((i:any)=>!i.manual&&!i.meaning&&i.semanticStatus!=='ready'
   &&Number(i.semanticAttempts||0)<UNCERTAIN_ATTEMPT_LIMIT&&Number(i.semanticFailures||0)<FAILURE_ATTEMPT_LIMIT
   &&Number(i.semanticRetryAt||0)<=Date.now()).slice(0,MAX_QUEUE_TICKS);
  for(const item of pending){
   const file=path.join(images,item.file);if(!/^qqf_[a-f0-9]{16}\.(gif|png)$/.test(item.file))continue;
   const info=await stat(file).catch(()=>null);if(!info||!info.isFile()||info.isSymbolicLink()||info.size>1048576)continue;
   const startedAt=Date.now(),before=await contentHash(file).catch(()=>null);
   let result:any,error:unknown;try{result=await this.caption(bot,file,signal)}catch(e){if(signal.aborted)throw e;error=e}
   const after=await contentHash(file).catch(()=>null);
   await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const latest=await readFavoriteIndex(this.root,bot.id);if(latest.account!==String(bot.qq_uin))return;
    const current=latest.items.find((i:any)=>i.id===item.id);if(!current||current.manual||current.meaning||current.file!==item.file)return;
    if(error){
     const message=String((error as Error)?.message||error);
     if(deferError(error)){current.semanticWaitReason=/chat_busy/.test(message)?'chat_busy':'pressure';current.semanticWaitMs=Number(current.semanticWaitMs||0)+Math.max(0,Date.now()-startedAt);current.semanticRetryAt=Date.now()+60000}
     else{current.semanticFailures=Number(current.semanticFailures||0)+1;current.semanticStatus='failed';current.lastVisionError=String((error as Error)?.message||error).slice(0,120);current.semanticRetryAt=Date.now()+Math.min(86400000,60000*2**Number(current.semanticFailures||0))}
    }
    else if(before!==after||!before){current.lastVisionError='favorite_content_changed';current.semanticRetryAt=Date.now()+60000}
    else if(result.confidence==='clear'){current.semantic=result;current.meaning=result.meaning;current.avoid=result.avoid;current.enabled=true;current.semanticStatus='ready';current.contentHash=after;delete current.lastVisionError;delete current.semanticRetryAt}
    else{current.semantic=result;current.semanticStatus='uncertain';current.semanticAttempts=Number(current.semanticAttempts||0)+1;current.semanticRetryAt=Date.now()+Math.min(86400000,60000*2**Number(current.semanticAttempts||0))}
    if(!current.queuedAt)current.queuedAt=startedAt;
    await writeFavoriteIndex(this.root,bot.id,latest);
   });
  }
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
   const info=await stat(path.join(images,String(item.id)+suffix)).catch(()=>null);
   if(info&&info.isFile()&&!info.isSymbolicLink()){item.fileReadable=true;break}
  }
 }
 const queued=items.filter((i:{manual:boolean;meaning:boolean;semanticStatus:string|null})=>!i.manual&&!i.meaning&&i.semanticStatus!=='ready');
 const oldestQueuedAt=Math.min(...queued.map((i:{queuedAt:number|null})=>Number(i.queuedAt||0)||Number.MAX_SAFE_INTEGER));
 return{botId,account:String(bot.qq_uin||''),indexAccount:index.account,accountMatched,revision:bot.revision,providerReady:Boolean(runtimeStatus(bot).online&&bot.config?.baseUrl&&bot.config?.model),memeEnabled:Boolean(bot.config?.behavior?.memeEnabled),qqFavoriteSaveEnabled:Boolean(bot.config?.behavior?.qqFavoriteSaveEnabled),indexEnabled:Boolean(index.enabled),queueSize:queued.length,oldestQueuedAt:Number.isFinite(oldestQueuedAt)?oldestQueuedAt:null,items};
}
