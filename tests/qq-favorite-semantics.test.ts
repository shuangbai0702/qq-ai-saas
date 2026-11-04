import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtemp,readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import Fastify from 'fastify';
import {createFavoriteCaptioner,parseFavoriteAnnotation} from '../apps/control-api/src/qq-favorite-semantics.js';
import {registerQQFavorites} from '../apps/control-api/src/qq-favorites.js';
import {encrypt} from '../packages/shared/security.js';

const clear={description:'角色抬起手挥动',meaning:'打招呼或回应到场',avoid:'严肃求助时不用',confidence:'clear' as const};
const annotation={...clear,version:1 as const,model:'fixture',at:new Date().toISOString(),usage:null,costStatus:'unknown' as const};
const barrier=()=>{let release!:()=>void;const promise=new Promise<void>(r=>release=r);return{promise,release}};
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
 const key=Buffer.alloc(32),bot={id:'fixture',generation:1,qq_uin:'90000'},record={...bot,tenant_id:'tenant',revision:2,runtime_mode:'dedicated',config:{model:'image-fixture',baseUrl:'https://model.example/v1'},ciphertext:encrypt(key,'tenant:fixture','fixture-key')};
 let revision=2,calls=0;
 const db={query:async(sql:string)=>sql.includes('JOIN bot_config_revisions')?[record]:[{...bot,revision}]};
 const caption=createFavoriteCaptioner(db as any,key,root,async(_url,init)=>{
  calls++;const payload=JSON.parse(String(init?.body));assert.equal(payload.model,'image-fixture');
  assert.match(payload.messages[1].content[1].image_url.url,/^data:image\/jpeg;base64,/);
  return new Response(JSON.stringify({choices:[{message:{content:JSON.stringify(clear)}}],usage:{total_tokens:123}}));
 });
 const result=await caption(bot,file,new AbortController().signal);assert.equal(result.meaning,clear.meaning);assert.deepEqual(result.usage,{total_tokens:123});assert.equal(result.costStatus,'unknown');
 revision=3;await assert.rejects(()=>caption(bot,file,new AbortController().signal),/stale/);assert.equal(calls,2);
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
