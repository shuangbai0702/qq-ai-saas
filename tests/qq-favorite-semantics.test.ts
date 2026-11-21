import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtemp,readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import Fastify from 'fastify';
import {createFavoriteCaptioner,parseFavoriteAnnotation,favoriteAnnotationDue,applyFavoriteAnnotation} from '../packages/shared/qq-favorite-semantics.js';
import {registerQQFavorites} from '../apps/control-api/src/qq-favorites.js';
import {encrypt} from '../packages/shared/security.js';

const clear={description:'角色抬起手挥动',meaning:'打招呼或回应到场',avoid:'严肃求助时不用',confidence:'clear' as const};
const annotation={...clear,version:1 as const,model:'fixture',at:new Date().toISOString(),usage:null,costStatus:'unknown' as const};
const barrier=()=>{let release!:()=>void;const promise=new Promise<void>(r=>release=r);return{promise,release}};
test('both collection entrypoints use one bounded retry and state transition contract',()=>{
 const item:any={meaning:'',enabled:false,semanticStatus:'pending',semanticFailures:0,semanticAttempts:0};
 assert.equal(favoriteAnnotationDue(item,1000),true);
 applyFavoriteAnnotation(item,undefined,Error('favorite_annotation_chat_busy'),1000,5);
 assert.equal(item.semanticFailures,0);assert.equal(favoriteAnnotationDue(item,1001),false);
 applyFavoriteAnnotation(item,undefined,Error('vision_failure'),62000);
 assert.equal(item.semanticFailures,1);assert.equal(item.lastVisionError,'vision_failure');
 applyFavoriteAnnotation(item,annotation,undefined,200000);
 assert.equal(item.semanticStatus,'ready');assert.equal(item.enabled,true);assert.equal(item.lastVisionError,undefined);assert.equal(item.semanticWaitReason,undefined);
 assert.equal(favoriteAnnotationDue(item,300000),false);
});
async function until(check:()=>Promise<boolean>){for(let i=0;i<200;i++){if(await check())return;await new Promise(r=>setTimeout(r,20))}throw Error('fixture timeout')}

test('favorite meaning parser rejects unsupported claims, empty clear meanings and non-JSON',()=>{
 assert.deepEqual(parseFavoriteAnnotation(JSON.stringify(clear)),clear);
 assert.throws(()=>parseFavoriteAnnotation(JSON.stringify({...clear,meaning:''})));
 assert.throws(()=>parseFavoriteAnnotation(JSON.stringify({...clear,confidence:'certain'})));
 assert.throws(()=>parseFavoriteAnnotation('```json\n'+JSON.stringify(clear)+'\n```'));
 assert.throws(()=>parseFavoriteAnnotation(JSON.stringify({...clear,sendTo:'other tenant'})));
});

test('favorite vision uses bounded real GIF samples, structured output and rejects a late identity change',async()=>{
 const root=await mkdtemp(path.resolve('.local/favorite-vision-')),file=path.join(root,'fixture.gif');
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 execFileSync(python,['-c',"from PIL import Image;import sys;f=[Image.new('RGB',(64,64),c) for c in ['red','green','blue']];f[0].save(sys.argv[1],save_all=True,append_images=f[1:],duration=100,loop=0)",file],{windowsHide:true});
 const preview=path.join(root,'preview.jpg'),meta=JSON.parse(execFileSync(python,['adapters/astrbot/favorite_media.py','--preview',file,preview],{encoding:'utf8',windowsHide:true}));
 assert.deepEqual(meta.sampledFrames,[0,1,2]);assert.equal(meta.audio,false);
 assert.ok((await readFile(preview)).length<131072);
 const key=Buffer.alloc(32),bot={id:'fixture',generation:1,qq_uin:'90000'},record={...bot,tenant_id:'tenant',revision:2,runtime_mode:'shared',group_id:'shared-group',config:{model:'main-fixture',favoriteModel:'image-fixture',baseUrl:'https://model.example/v1'},ciphertext:encrypt(key,'tenant:fixture','fixture-key')};
 await mkdir(path.join(root,'bots','shared-group','ai'),{recursive:true});
 await writeFile(path.join(root,'bots','shared-group','ai','memory-activity.json'),JSON.stringify({active:true,at:Date.now()/1000}));
 let revision=2,calls=0;
 const db={query:async(sql:string)=>sql.includes('JOIN bot_config_revisions')?[record]:[{...bot,revision}]};
 const caption=createFavoriteCaptioner(db as any,key,root,async(_url,init)=>{
  calls++;const payload=JSON.parse(String(init?.body));assert.equal(payload.model,'image-fixture');
  assert.match(payload.messages[1].content[1].image_url.url,/^data:image\/jpeg;base64,/);
  return new Response(JSON.stringify({choices:[{message:{content:JSON.stringify(clear)}}],usage:{total_tokens:123}}));
 });
 const result=await caption(bot,file,new AbortController().signal);assert.equal(result.meaning,clear.meaning);assert.deepEqual(result.usage,{total_tokens:123});assert.equal(result.costStatus,'unknown');
 const activity=path.join(root,'bots',bot.id,'memory-activity.json');await mkdir(path.dirname(activity),{recursive:true});
 await writeFile(activity,JSON.stringify({active:true,at:Date.now()/1000}));
 await assert.rejects(()=>caption(bot,file,new AbortController().signal),/chat_busy/);assert.equal(calls,1,'only this Bot can defer its own annotation, before a model request');
 await writeFile(activity,JSON.stringify({active:false,at:Date.now()/1000}));
 revision=3;await assert.rejects(()=>caption(bot,file,new AbortController().signal),/stale/);assert.equal(calls,2);
 const entered=barrier(),resume=barrier();
 const parallel=createFavoriteCaptioner({query:async(sql:string,args:any[])=>sql.includes('JOIN bot_config_revisions')?[{...record,id:args[0],ciphertext:encrypt(key,'tenant:'+args[0],'fixture-key')}]:[{...bot,id:args[0],revision:2}]} as any,key,root,async(_url,init)=>{
  if((init?.headers as Record<string,string>).Authorization==='Bearer fixture-key'){
   const payload=JSON.parse(String(init?.body));if(!enteredFirst){enteredFirst=true;entered.release();await resume.promise}
   assert.equal(payload.model,'image-fixture');
  }
  return new Response(JSON.stringify({choices:[{message:{content:JSON.stringify(clear)}}]}));
 });
 let enteredFirst=false;const slow=parallel(bot,file,new AbortController().signal);await entered.promise;
 let timeout:ReturnType<typeof setTimeout>|undefined;
 try{await Promise.race([parallel({...bot,id:'other'},file,new AbortController().signal),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('unrelated Bot blocked by account queue')),2000)})])}
 finally{if(timeout)clearTimeout(timeout);resume.release();await slow}
});

test('cached missing meanings are annotated once; uncertain results remain unavailable; manual edits beat late AI',async()=>{
 const root=await mkdtemp(path.resolve('.local/favorite-annotation-')),folder=path.join(root,'bots','a','qq-favorites'),images=path.join(folder,'images');await mkdir(images,{recursive:true});
 const bot={id:'a',protocol:'napcat',qq_uin:'90000',generation:1,desired_state:'running',health:{protocol:{online:true}}};
 const {createHash}=await import('node:crypto'),ids=['clear','uncertain','manual'].map(name=>'qqf_'+createHash('sha256').update(name).digest('hex').slice(0,16));
 await Promise.all(ids.map(id=>writeFile(path.join(images,id+'.png'),Buffer.from([137,80,78,71]))));
 await writeFile(path.join(folder,'index.json'),JSON.stringify({account:'90000',enabled:true,items:ids.map(id=>({id,file:id+'.png',mime:'image/png',meaning:'',avoid:'',enabled:false,animated:false}))}));
 const app=Fastify({logger:{level:'warn'}}),entered=barrier(),resume=barrier();let calls=0;
 registerQQFavorites(app,root,async()=>({...bot}),{call:async(_r,_b,action)=>action==='get_login_info'?{user_id:'90000'}:['clear','uncertain','manual'].map(md5=>({md5,url:'https://qpic.cn/'+md5})),download:async()=>{throw Error('cached image redownloaded')},caption:async(_b,file)=>{calls++;if(file.endsWith(ids[2]+'.png')){entered.release();await resume.promise}return file.endsWith(ids[1]+'.png')?{...annotation,confidence:'uncertain'}:annotation}});
 const url='/v1/bots/a/qq-favorites',get=async()=>(await app.inject({url})).json();
 try{
  assert.equal((await app.inject({url:url+'/sync',method:'POST'})).statusCode,202);await entered.promise;
  const current=await get();assert.equal((await app.inject({url:url+'/'+ids[2],method:'PATCH',payload:{expectedVersion:current.version,meaning:'用户手写的场景',enabled:false}})).statusCode,200);
  resume.release();await until(async()=>!(await get()).syncing);
  const data=await get();assert.equal(data.items[0].meaning,clear.meaning);assert.equal(data.items[0].enabled,true);
  assert.equal(data.items[1].meaning,'');assert.equal(data.items[1].enabled,false);assert.equal(data.items[1].semanticStatus,'uncertain');
  assert.equal(data.items[2].meaning,'用户手写的场景');assert.equal(data.items[2].enabled,false);assert.equal(data.items[2].semantic,undefined);
  assert.equal((await app.inject({url:url+'/sync',method:'POST'})).statusCode,202);await until(async()=>!(await get()).syncing);assert.equal(calls,3,'cached clear, uncertain and manual results must not be re-billed');
 }finally{resume.release();await app.close()}
});
