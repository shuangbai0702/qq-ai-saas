import {execFileSync} from 'node:child_process';
import test from 'node:test';import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';import path from 'node:path';
import {FavoriteMaintenance,favoriteQueueDiagnostics} from '../apps/node-agent/src/favorite-maintenance.js';
import {withFavoriteIndex,readFavoriteIndex,writeFavoriteIndex,favoriteDirectory} from '../packages/shared/qq-favorite-index.js';

test('recent saved original wakes semantic discovery without claiming a confirmed collection',async()=>{
 const {root,bot,owner}=await fixture();const digest='a'.repeat(32),id='qqf_'+createHash('sha256').update(digest).digest('hex').slice(0,16);
 const directory=path.join(favoriteDirectory(root,'a'),'uploads');await mkdir(directory);await writeFile(path.join(directory,digest+'.image'),'original');
 assert.equal(await owner.recentSavePending(bot),true);
 owner.caption=async()=>semantic;await owner.run(new AbortController().signal);await owner.job;
 let index=await readFavoriteIndex(root,'a');assert.equal(index.items.some((item:any)=>item.id===id),false,'unconfirmed upload cannot enter the catalog');
 index.items.push({id,file:id+'.png',meaning:'',enabled:false,semanticStatus:'pending',semanticRetryAt:0});await writeFavoriteIndex(root,'a',index);
 assert.equal(await owner.recentSavePending(bot),true);
 index.items.at(-1).semanticRetryAt=Date.now()+60000;await writeFavoriteIndex(root,'a',index);assert.equal(await owner.recentSavePending(bot),false);
 index.items.at(-1).semanticRetryAt=0;index.items.at(-1).meaning='贴贴';index.items.at(-1).semanticStatus='ready';await writeFavoriteIndex(root,'a',index);
 assert.equal(await owner.recentSavePending(bot),false,'ready image does not create repeated discovery');
});

test('slow accounts do not serialize other accounts; stop drains and prevents late writes',async()=>{
 const {bot,owner}=await fixture();const bots=['a','b','c','d'].map(id=>({...bot,id}));
 owner.db={query:async()=>bots} as any;
 owner.recentSavePending=async()=>false;
 const started:string[]=[];let release!:()=>void;const slow=new Promise<void>(r=>release=r);
 owner.maintain=async(b,signal)=>{started.push(b.id);if(b.id==='a')await slow;signal.throwIfAborted()};
 await owner.run(owner.controller.signal);await new Promise(r=>setImmediate(r));
 assert.deepEqual(started,['a','b','c']);
 await owner.run(owner.controller.signal);await new Promise(r=>setImmediate(r));
 assert.deepEqual(started,['a','b','c','d'],'completed accounts release slots without waiting for slow a');
 let stopped=false;const stopping=owner.stop().then(()=>{stopped=true});await new Promise(r=>setImmediate(r));
 assert.equal(stopped,false);release();await stopping;assert.equal(owner.job,undefined);
 await owner.run(new AbortController().signal);assert.equal(started.length,4,'closed owner cannot launch another account');
});

test('fresh confirmed imports are annotated before the older backlog in the same job',async()=>{
 const {root,bot,owner}=await fixture();const index=await readFavoriteIndex(root,'a');index.items.push({...index.items[0],id:'qqf_aaaaaaaaaaaaaaaa',file:'qqf_aaaaaaaaaaaaaaaa.png'});
 await writeFile(path.join(favoriteDirectory(root,'a'),'images','qqf_aaaaaaaaaaaaaaaa.png'),'old');await writeFavoriteIndex(root,'a',index);
 const digest='b'.repeat(32),id='qqf_'+createHash('sha256').update(digest).digest('hex').slice(0,16);
 owner.deps.call=(async(_r:string,_b:any,action:string)=>action==='get_login_info'?{user_id:90000}:[{md5:digest,url:'https://qpic.cn/fixture'}]) as any;
 const seen:string[]=[];owner.caption=async(_bot,file)=>{seen.push(path.basename(file));return semantic};
 await owner.maintain(bot,new AbortController().signal);
 const after=await readFavoriteIndex(root,'a');assert.equal(seen[0],id+'.png');assert.equal(after.items.find((item:any)=>item.id===id).semanticStatus,'ready');
});

test('unsupported imports cannot starve cached annotations and import attempts stay bounded',async()=>{
 const {root,bot,owner}=await fixture();let downloads=0,captions=0;
 owner.deps.call=(async(_r:string,_b:any,action:string)=>action==='get_login_info'?{user_id:90000}:Array.from({length:10},(_,n)=>({md5:'bad'+n,url:'https://qpic.cn/unsupported'}))) as any;
 owner.deps.download=async()=>{downloads++;if(downloads<=4)throw Error('unsupported_fixture');return Buffer.from('valid')};
 owner.caption=async()=>{captions++;return semantic};
 await owner.maintain(bot,new AbortController().signal);
 const after=await readFavoriteIndex(root,'a');assert.equal(downloads,4);assert.equal(captions,1);
 assert.equal(after.items[0].semanticStatus,'ready');assert.equal(after.lastImportFailure.error,'unsupported_fixture');
 assert.equal(after.items.length,1,'a failed import must not invent an image record');
 await owner.maintain(bot,new AbortController().signal);
 const progressed=await readFavoriteIndex(root,'a');const fifth='qqf_'+createHash('sha256').update('bad4').digest('hex').slice(0,16);
 assert.equal(downloads,8);assert.ok(progressed.items.some((i:any)=>i.id===fifth),'failed first four must not starve later originals');
});

test('import storage failure and replaced owner are not downgraded into per-image failures',async()=>{
 for(const kind of ['storage','owner']){
  const {root,bot,owner}=await fixture();
  owner.deps.call=(async(_r:string,_b:any,action:string)=>action==='get_login_info'?{user_id:90000}:[{md5:'bad',url:'https://qpic.cn/fixture'}]) as any;
  owner.deps.download=async()=>{if(kind==='owner')bot.revision++;throw Object.assign(Error('fixture'),kind==='storage'?{code:'ENOSPC'}:{})};
  await assert.rejects(()=>owner.maintain({...bot},new AbortController().signal));
  const after=await readFavoriteIndex(root,'a');assert.equal(after.lastImportFailure,undefined);assert.equal(after.items[0].semanticStatus,'pending');
 }
});
const semantic={description:'挥手',meaning:'欢迎',avoid:'严肃时',confidence:'clear' as const,version:1 as const,model:'fixture',at:'now',usage:null,costStatus:'unknown' as const};
const uncertain={description:'角色抬手，手势含义不明确',meaning:'',avoid:'',confidence:'uncertain' as const,version:1 as const,model:'fixture',at:'now',usage:null,costStatus:'unknown' as const};
async function fixture(){
 const root=await mkdtemp(path.resolve('.local/favorite-maintenance-')),bot={id:'a',tenant_id:'tenant',generation:1,revision:1,qq_uin:'90000',desired_state:'running',health:{sampledAt:new Date().toISOString(),protocol:{online:true}},config:{baseUrl:'https://fixture.test/v1',model:'fixture-model',behavior:{memeEnabled:true,qqFavoriteSaveEnabled:true}}};
 const db={query:async()=>[bot]},owner=new FavoriteMaintenance(db as any,root,Buffer.alloc(32),{call:async(_r,_b,action)=>action==='get_login_info'?{user_id:90000}:[],download:async()=>Buffer.from(''),convert:async(input:string,output:string)=>{await writeFile(output+'.png','converted');return{suffix:'.png',mime:'image/png',animated:false}}});
 const folder=path.join(favoriteDirectory(root,'a'),'images');await mkdir(folder,{recursive:true});await writeFile(path.join(folder,'qqf_1234567890abcdef.png'),'fixture');
 await writeFavoriteIndex(root,'a',{account:'90000',enabled:true,items:[{id:'qqf_1234567890abcdef',file:'qqf_1234567890abcdef.png',meaning:'',enabled:false,semanticStatus:'pending',semanticAttempts:0,semanticFailures:0,semanticRetryAt:0}]});
 return{root,bot,owner};
}
test('pending annotations resume from the same index after failure and restart',async()=>{
 const {root,bot,owner}=await fixture();owner.caption=async()=>{throw Error('vision_failure')};await owner.maintain(bot,new AbortController().signal);
 let index=await readFavoriteIndex(root,'a');assert.equal(index.items[0].semanticFailures,1);assert.ok(index.items[0].semanticRetryAt>Date.now());
 index.items[0].semanticRetryAt=0;await writeFavoriteIndex(root,'a',index);
 const next=new FavoriteMaintenance(owner.db,root,Buffer.alloc(32),owner.deps);next.caption=async()=>semantic;await next.maintain(bot,new AbortController().signal);
 index=await readFavoriteIndex(root,'a');assert.equal(index.items[0].meaning,'欢迎');assert.equal(index.items[0].semanticStatus,'ready');assert.equal(index.enabled,true);
});

test('missing originals cannot consume the annotation batch and starve later valid images',async()=>{
 const {root,bot,owner}=await fixture();const index=await readFavoriteIndex(root,'a'),valid=index.items[0];
 index.items=[{...valid,id:'qqf_aaaaaaaaaaaaaaaa',file:'qqf_aaaaaaaaaaaaaaaa.png'},{...valid,id:'qqf_bbbbbbbbbbbbbbbb',file:'qqf_bbbbbbbbbbbbbbbb.png'},valid];
 await writeFavoriteIndex(root,'a',index);
 let calls=0;owner.caption=async()=>{calls++;return semantic};
 await owner.maintain(bot,new AbortController().signal);
 const after=await readFavoriteIndex(root,'a');assert.equal(calls,1);assert.equal(after.items[2].semanticStatus,'ready');
 assert.equal(after.items[0].semanticFailures,0,'unavailable files must not consume model retries');
});

test('catalog discovery failure is recorded but cached images still receive annotation',async()=>{
 const {root,bot,owner}=await fixture();let calls=0;
 owner.deps.call=async(_r,_b,action)=>{if(action==='get_login_info')return {user_id:90000};throw Error('catalog_temporarily_unavailable')};
 owner.caption=async()=>{calls++;return semantic};
 await owner.maintain(bot,new AbortController().signal);
 const index=await readFavoriteIndex(root,'a');assert.equal(calls,1);assert.equal(index.items[0].semanticStatus,'ready');
 assert.equal(index.lastDiscoveryFailure.error,'catalog_temporarily_unavailable');
});

test('canceled catalog discovery cannot persist diagnostics or annotate from a stale owner',async()=>{
 const {root,bot,owner}=await fixture(),controller=new AbortController();let calls=0;
 const before=await readFavoriteIndex(root,'a');
 owner.deps.call=async(_r,_b,action)=>{if(action==='get_login_info')return {user_id:90000};controller.abort();throw Error('canceled')};
 owner.caption=async()=>{calls++;return semantic};
 await assert.rejects(owner.maintain(bot,controller.signal));
 assert.equal(calls,0);assert.deepEqual(await readFavoriteIndex(root,'a'),before);
});

test('queue diagnostics leave an unknown oldest timestamp null rather than inventing a far-future date',async()=>{
 const {root,owner}=await fixture();const before=await readFavoriteIndex(root,'a');
 const info=await favoriteQueueDiagnostics(owner.db,root,'a');assert.ok(info);assert.equal(info.oldestQueuedAt,null);
 assert.deepEqual(await readFavoriteIndex(root,'a'),before,'diagnostics stay read-only');
});
test('manual edits win over an in-flight caption across index owners',async()=>{
 const {root,bot,owner}=await fixture();let release!:()=>void,entered!:()=>void;const start=new Promise<void>(r=>entered=r),wait=new Promise<void>(r=>release=r);
 owner.caption=async()=>{entered();await wait;return semantic};const task=owner.maintain(bot,new AbortController().signal);await start;
 await withFavoriteIndex(root,'a',async()=>{const index=await readFavoriteIndex(root,'a');index.items[0].manual=true;index.items[0].meaning='手工';index.items[0].enabled=false;await writeFavoriteIndex(root,'a',index)});release();await task;
 const index=await readFavoriteIndex(root,'a');assert.equal(index.items[0].meaning,'手工');assert.equal(index.items[0].enabled,false);
});
test('configuration replacement and cancellation cannot publish late annotation',async()=>{
 for(const kind of ['revision','cancel']){const {root,bot,owner}=await fixture();const controller=new AbortController();owner.caption=async()=>{if(kind==='revision')bot.revision++;else controller.abort();return semantic};
 // Pass a captured owner, not the mutable fixture database row.
 await assert.rejects(()=>owner.maintain({...bot},controller.signal));const index=await readFavoriteIndex(root,'a');assert.equal(index.items[0].meaning,'');}
});
test('cross-process index lock serializes read-modify-write owners',async()=>{
 const {root}=await fixture();await Promise.all(Array.from({length:5},()=>withFavoriteIndex(root,'a',async()=>{const index=await readFavoriteIndex(root,'a');await new Promise(r=>setTimeout(r,15));index.counter=(index.counter||0)+1;await writeFavoriteIndex(root,'a',index)})));
 assert.equal((await readFavoriteIndex(root,'a')).counter,5);
});
test('qq raw text is stored as rawText and never enables automatic selection',async()=>{
 const {root,bot,owner}=await fixture();
 const call=owner.deps.call;owner.deps.call=(async(root:string,b:any,action:string,body:any,signal:AbortSignal)=>{
  if(action==='fetch_custom_face_detail')return [{md5:'fingerprintraw',modifyWord:'开心',desc:'',ocrWord:''}];
  return call(root,b,action,body,signal)}) as any;
 owner.caption=async()=>{throw Error('favorite_annotation_pressure')};
 await owner.maintain(bot,new AbortController().signal);
 const index=await readFavoriteIndex(root,'a');const item=index.items.find((i:any)=>i.id==='qqf_'+createHash('sha256').update('fingerprintraw').digest('hex').slice(0,16));
 assert.ok(item,'raw-text favorite was imported');assert.equal(item.rawText,'开心');assert.equal(item.rawSource,'qq_text');
 assert.equal(item.meaning,'');assert.equal(item.enabled,false);assert.equal(item.semanticStatus,'raw_text');
 assert.equal(item.semanticAttempts,0);assert.equal(item.semanticFailures,0);assert.equal(item.semanticWaitReason,'pressure');assert.ok(item.semanticWaitMs>=0);
 // The legacy vision annotation is still the only automatic enablement path.
 await withFavoriteIndex(root,'a',async()=>{const current=await readFavoriteIndex(root,'a');const target=current.items.find((i:any)=>i.id===item.id);target.semanticRetryAt=0;await writeFavoriteIndex(root,'a',current)});
 const next=new FavoriteMaintenance(owner.db,root,Buffer.alloc(32),owner.deps);next.caption=async()=>semantic;await next.maintain(bot,new AbortController().signal);
 const after=await readFavoriteIndex(root,'a');const annotated=after.items.find((i:any)=>i.id===item.id);
 assert.equal(annotated.meaning,'欢迎');assert.equal(annotated.enabled,true);assert.equal(annotated.semanticStatus,'ready');assert.equal(annotated.contentHash,createHash('sha256').update('converted').digest('hex'));
});
test('uncertain interpretation is selectable by picture description without repeated vision',async()=>{
 const {root,bot,owner}=await fixture();let calls=0;
 owner.caption=async()=>{calls++;return uncertain};
 for(let n=0;n<4;n++){await owner.maintain(bot,new AbortController().signal);const index=await readFavoriteIndex(root,'a');index.items[0].semanticRetryAt=0;await writeFavoriteIndex(root,'a',index)}
 const index=await readFavoriteIndex(root,'a');
 assert.equal(index.items[0].semanticAttempts,0);assert.equal(index.items[0].semanticStatus,'ready');assert.equal(calls,1,'a usable description must not trigger repeated vision');
 assert.equal(index.items[0].meaning,'画面：'+uncertain.description);assert.equal(index.items[0].enabled,true);assert.equal(index.items[0].semantic.confidence,'uncertain');
});
test('stored uncertain descriptions are adopted once without vision and manual disablement wins',async()=>{
 const {root,bot,owner}=await fixture();const index=await readFavoriteIndex(root,'a'),item=index.items[0];
 Object.assign(item,{semantic:{...uncertain,meaning:'猜测为嘲笑',avoid:'猜测场景'},semanticStatus:'uncertain',semanticAttempts:3,enabled:false});
 const manual={...item,id:'qqf_aaaaaaaaaaaaaaaa',file:'qqf_aaaaaaaaaaaaaaaa.png',manual:true};index.items.push(manual);
 await writeFile(path.join(favoriteDirectory(root,'a'),'images',manual.file),'manual');await writeFavoriteIndex(root,'a',index);
 owner.caption=async()=>{throw Error('cached descriptions must not be billed again')};
 await owner.maintain(bot,new AbortController().signal);const first=await readFavoriteIndex(root,'a');
 assert.equal(first.items[0].meaning,'画面：'+uncertain.description);assert.equal(first.items[0].avoid,'');assert.equal(first.items[0].enabled,true);assert.equal(first.items[0].semantic.confidence,'uncertain');
 assert.equal(first.items[1].enabled,false);assert.equal(first.items[1].manual,true);
 await owner.maintain(bot,new AbortController().signal);assert.deepEqual((await readFavoriteIndex(root,'a')).items,first.items);
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const probe=`import sys,json
sys.path.insert(0,'adapters/astrbot')
from qq_favorites import catalog,descriptions
rows=catalog(sys.argv[1],'90000');assert len(rows)==1
assert rows[0]['meaning'].startswith('画面：') and rows[0]['avoid']==''
assert descriptions(sys.argv[1],'90000')[rows[0]['id']]['meaning']==rows[0]['meaning']
print(json.dumps({'selected':rows[0]['id']}))`;
 assert.equal(JSON.parse(execFileSync(python,['-B','-c',probe,path.join(root,'bots/a')],{encoding:'utf8',windowsHide:true})).selected,item.id);
});

test('pressure and chat-busy defer without consuming attempts and record waiting evidence',async()=>{
 const {root,bot,owner}=await fixture();let calls=0;
 owner.caption=async()=>{calls++;throw Error('favorite_annotation_chat_busy')};
 for(let n=0;n<3;n++){await owner.maintain(bot,new AbortController().signal);const index=await readFavoriteIndex(root,'a');index.items[0].semanticRetryAt=0;await writeFavoriteIndex(root,'a',index)}
 const index=await readFavoriteIndex(root,'a');
 assert.equal(calls,3);assert.equal(index.items[0].semanticAttempts,0);assert.equal(index.items[0].semanticFailures,0);
 assert.equal(index.items[0].semanticWaitReason,'chat_busy');assert.equal(index.items[0].semanticStatus,'pending');
});
test('legacy raw-text-enabled items are demoted at the maintenance boundary and keep their file',async()=>{
 const {root,bot,owner}=await fixture();
 await withFavoriteIndex(root,'a',async()=>{const index=await readFavoriteIndex(root,'a');index.items[0]={...index.items[0],meaning:'旧文字',enabled:true,semanticStatus:'ready'};await writeFavoriteIndex(root,'a',index)});
 let calls=0;owner.caption=async()=>{calls++;return uncertain};
 await owner.maintain(bot,new AbortController().signal);
 const index=await readFavoriteIndex(root,'a');const item=index.items[0];
 assert.equal(item.meaning,'画面：'+uncertain.description);assert.equal(item.enabled,true);assert.equal(item.rawText,'旧文字');assert.equal(item.rawSource,'legacy_text');
 assert.equal(item.semanticStatus,'ready');assert.equal(item.semanticAttempts,0,'a useful description needs no interpretation retry');
 assert.equal(calls,1);
 await assert.doesNotReject(readFile(path.join(favoriteDirectory(root,'a'),'images',item.file)),'the original picture must be kept');
});
test('a vision clear result whose content hash changed is not published',async()=>{
 const {root,bot,owner}=await fixture();const file=path.join(favoriteDirectory(root,'a'),'images','qqf_1234567890abcdef.png');
 owner.caption=async()=>{await writeFile(file,'replaced-bytes');return semantic};
 await owner.maintain(bot,new AbortController().signal);
 const index=await readFavoriteIndex(root,'a');assert.equal(index.items[0].meaning,'');assert.equal(index.items[0].enabled,false);assert.equal(index.items[0].lastVisionError,'favorite_content_changed');
});
test('queue diagnostics expose evidence fields without changing any gate',async()=>{
 const {root,bot,owner}=await fixture();owner.caption=async()=>uncertain;await owner.maintain(bot,new AbortController().signal);
 const info=await favoriteQueueDiagnostics(owner.db as any,root,'a');
 assert.ok(info);assert.equal(info.accountMatched,true);assert.equal(info.providerReady,true);assert.equal(info.revision,1);
 assert.equal(info.queueSize,0);const item=info.items[0];
 assert.equal(item.uncertainAttempts,0);assert.equal(item.manual,false);assert.ok(item.fileReadable,'image readability is part of the diagnosis');
});


test('automatic clear annotation is immediately selectable without approval and ignores legacy send caps',async()=>{
 const {root,bot,owner}=await fixture();owner.caption=async()=>semantic;
 await owner.maintain(bot,new AbortController().signal);
 const index=await readFavoriteIndex(root,'a');assert.equal(index.items[0].enabled,true);assert.equal(index.items[0].manual,undefined);
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const probe=String.raw`import sys,json,sqlite3
sys.path.insert(0,'adapters/astrbot')
from social import SocialStore
from qq_favorites import catalog
root=sys.argv[1];s=SocialStore(sqlite3.connect(':memory:'),root)
cfg={'memeEnabled':True,'memePacks':[],'_qqAccount':'90000','memeCooldownSeconds':3600,'dailyMemeLimit':0,'memeProbability':0}
items=catalog(root,'90000');assert len(items)==1
s.reserve_image('private:12345',1000)
clean,image=s.select_image('[MEME:'+items[0]['id']+']','private:12345',cfg,1000,candidates=items)
assert image and image['id']==items[0]['id']
assert catalog(root,'90001')==[]
s.close()
print(json.dumps({'selected':image['id'],'approved':False}))`;
 const out=JSON.parse(execFileSync(python,['-B','-c',probe,path.join(root,'bots','a')],{encoding:'utf8',windowsHide:true}));
 assert.equal(out.selected,index.items[0].id);assert.equal(out.approved,false);
});

test('maintenance restores an exhausted but validated stored description through its index owner',async()=>{
 const {root,bot,owner}=await fixture();const index=await readFavoriteIndex(root,'a');
 Object.assign(index.items[0],{semantic:uncertain,semanticStatus:'failed',semanticFailures:5,lastVisionError:'Unterminated string in JSON'});
 await writeFavoriteIndex(root,'a',index);owner.caption=async()=>{throw Error('must not rebill stored evidence')};
 await owner.maintain(bot,new AbortController().signal);
 const after=await readFavoriteIndex(root,'a');assert.equal(after.items[0].semanticStatus,'ready');assert.equal(after.items[0].meaning,'画面：'+uncertain.description);assert.equal(after.items[0].semantic.confidence,'uncertain');assert.equal(after.items.length,index.items.length);
});
