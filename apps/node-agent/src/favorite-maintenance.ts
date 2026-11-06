/** Bounded maintenance on the existing Agent tick, no per-bot timer/Agent. */
import {mkdir,writeFile,rm,rename,lstat,readdir} from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';
import {createFavoriteCaptioner} from '../../../packages/shared/qq-favorite-semantics.js';
import {downloadFavorite,nativeCall,normalize} from '../../../packages/shared/qq-favorite-media.js';
import {favoriteDirectory,readFavoriteIndex,writeFavoriteIndex,withFavoriteIndex} from '../../../packages/shared/qq-favorite-index.js';
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
     const meaning=String(source.modifyWord||source.desc||source.ocrWord||'').replace(/[\x00-\x1f\x7f]/g,' ').trim().slice(0,100),file=id+result.suffix;
     await rename(path.join(stage,file),path.join(images,file));
     index.account=String(bot.qq_uin);index.items.push({id,file,mime:result.mime,animated:result.animated,meaning,avoid:'',enabled:!!meaning,semanticStatus:meaning?'ready':'pending'});
     // Existing explicit selection preference is never changed by discovery.
     await writeFavoriteIndex(this.root,bot.id,index);imported++;
    });
   }finally{await rm(stage,{recursive:true,force:true})}
  }
  const index=await readFavoriteIndex(this.root,bot.id);if(index.account!==String(bot.qq_uin))return;
  const pending=index.items.filter((i:any)=>!i.manual&&!i.meaning&&(i.semanticStatus!=='uncertain')&&Number(i.semanticAttempts||0)<5&&Number(i.semanticRetryAt||0)<=Date.now()).slice(0,2);
  for(const item of pending){
   const file=path.join(images,item.file);if(!/^qqf_[a-f0-9]{16}\.(gif|png)$/.test(item.file))continue;
   const stat=await lstat(file);if(!stat.isFile()||stat.isSymbolicLink()||stat.size>1048576)continue;
   let result:any,error:unknown;try{result=await this.caption(bot,file,signal)}catch(e){if(signal.aborted)throw e;error=e}
   await withFavoriteIndex(this.root,bot.id,async()=>{await this.valid(bot,signal);const latest=await readFavoriteIndex(this.root,bot.id);if(latest.account!==String(bot.qq_uin))return;
    const current=latest.items.find((i:any)=>i.id===item.id);if(!current||current.manual||current.meaning||current.file!==item.file)return;
    if(error){const deferred=/pressure|chat_busy/.test(String(error));current.semanticAttempts=Number(current.semanticAttempts||0)+(deferred?0:1);current.semanticStatus='failed';current.semanticRetryAt=Date.now()+Math.min(86400000,60000*2**Number(current.semanticAttempts||0))}
    else{current.semantic=result;current.semanticStatus=result.confidence==='clear'?'ready':'uncertain';if(result.confidence==='clear'){current.meaning=result.meaning;current.avoid=result.avoid;current.enabled=true}delete current.semanticRetryAt}
    await writeFavoriteIndex(this.root,bot.id,latest);
   });
  }
 }
}
