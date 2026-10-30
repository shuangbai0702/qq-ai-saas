import test from 'node:test';import assert from 'node:assert/strict';import Fastify from 'fastify';import {mkdtemp,readFile,writeFile,mkdir} from 'node:fs/promises';import path from 'node:path';import {execFileSync} from 'node:child_process';
import {registerQQFavorites,downloadFavorite} from '../apps/control-api/src/qq-favorites.js';import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
async function until(check:()=>Promise<boolean>){for(let i=0;i<200;i++){if(await check())return;await new Promise(r=>setTimeout(r,25))}throw new Error('Favorite sync deadline')}
test('QQ favorite sync preserves animation, settings, ownership and rejects stale account completion',async()=>{
 const root=await mkdtemp(path.resolve('.local/favorites-native-')),app=Fastify(),bot={id:'a',protocol:'napcat',qq_uin:'90000',generation:1,desired_state:'running',health:{protocol:{online:true}}};let identity='90000',downloads=0,release:(()=>void)|undefined,entered=false,hold=false;
 const index=JSON.parse(await readFile('templates/meme-packs/blue-fat-fish/index.json','utf8')),gif=await readFile(path.join('templates/meme-packs/blue-fat-fish',index.items[0].file));
 registerQQFavorites(app,root,async(req,id)=>{if(req.headers['x-owner']!=='a'||id!=='a')throw Object.assign(new Error('Not owned'),{statusCode:404});return {...bot}},{call:async(_root,_bot,action,params)=>{if(action==='get_login_info')return{user_id:identity};assert.equal((params as any).count,200);return[{md5:'favorite-one',url:'https://gchat.qpic.cn/favorite',desc:'开心时表示亲近'}]},download:async()=>{downloads++;if(hold){entered=true;await new Promise<void>(r=>release=r)}return gif}});
 const url='/v1/bots/a/qq-favorites',headers={'x-owner':'a'},get=async()=>(await app.inject({url,headers})).json();
 try{
  assert.equal((await app.inject({url})).statusCode,404);hold=true;assert.equal((await app.inject({url:url+'/sync',method:'POST',headers})).statusCode,202);await until(async()=>entered);
  assert.equal((await app.inject({url:url+'/sync',method:'POST',headers})).statusCode,409);release!();hold=false;await until(async()=>!(await get()).syncing);
  let data=await get();assert.equal(data.enabled,false);assert.equal(data.items.length,1);const item=data.items[0];assert.equal(item.animated,true);assert.equal(item.enabled,true);const image=await app.inject({url:url+'/'+item.id,headers});assert.equal(image.headers['content-type'],'image/gif');assert.ok(image.rawPayload.length<1048576);
  const payload={expectedVersion:data.version,enabled:false,meaning:'只在互相开玩笑时用'};assert.equal((await app.inject({url:url+'/'+item.id,method:'PATCH',headers,payload})).statusCode,200);assert.equal((await app.inject({url:url+'/'+item.id,method:'PATCH',headers,payload})).statusCode,409);
  assert.equal((await app.inject({url:url+'/sync',method:'POST',headers})).statusCode,202);await until(async()=>!(await get()).syncing);data=await get();assert.equal(data.items[0].enabled,false);assert.equal(data.items[0].meaning,payload.meaning);assert.equal(downloads,1,'cached favorite not redownloaded');
  data=await get();await app.inject({url:url+'/'+item.id,method:'PATCH',headers,payload:{expectedVersion:data.version,enabled:true}});
  const catalog=(account:string)=>JSON.parse(execFileSync(python,['-c',"import sys,json;sys.path.insert(0,'adapters/astrbot');from qq_favorites import catalog;print(json.dumps(catalog(sys.argv[1],sys.argv[2])))",path.join(root,'bots/a'),account],{encoding:'utf8',windowsHide:true}));
  assert.equal(catalog('90000').length,0,'favorites opt-in is off by default');data=await get();assert.equal((await app.inject({url:url+'/settings',method:'PATCH',headers,payload:{expectedVersion:data.version,enabled:true}})).statusCode,200);
  assert.equal(catalog('90000').length,1);assert.equal(catalog('80000').length,0);
  // A different QQ cannot inherit the cached images or receive delayed writes.
  bot.qq_uin='80000';identity='80000';bot.generation++;assert.equal((await get()).items.length,0);assert.equal((await app.inject({url:url+'/'+item.id,headers})).statusCode,404);
  entered=false;hold=true;assert.equal((await app.inject({url:url+'/sync',method:'POST',headers})).statusCode,202);await until(async()=>entered);bot.qq_uin='70000';identity='70000';bot.generation++;release!();await until(async()=>!(await get()).syncing);assert.equal((await get()).items.length,0);assert.ok((await get()).error);
 }finally{release?.();await app.close()}
});
test('real SaaS favorites routes authorize cached media, edits and sync against bot ownership',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/favorites-auth-')),agent:false});try{
  const cookies:Array<{fish_session:string}>=[];for(const name of ['a','b']){const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:name+'@favorites.test',name,password:'favorites-test-123'}});cookies.push({fish_session:r.cookies[0].value})}
  const id=(await current.app.inject({url:'/v1/bots',method:'POST',cookies:cookies[0],payload:{name:'收藏权限'}})).json().id,url='/v1/bots/'+id+'/qq-favorites';
  assert.equal((await current.app.inject({url})).statusCode,401);for(const route of [url,url+'/qqf_0123456789abcdef'])assert.equal((await current.app.inject({url:route,cookies:cookies[1]})).statusCode,404);
  assert.equal((await current.app.inject({url:url+'/sync',method:'POST',cookies:cookies[1]})).statusCode,404);assert.equal((await current.app.inject({url:url+'/settings',method:'PATCH',cookies:cookies[1],payload:{expectedVersion:'a'.repeat(64),enabled:false}})).statusCode,404);
  assert.equal((await current.app.inject({url:url+'/sync',method:'POST',cookies:cookies[0]})).statusCode,409);assert.equal((await current.app.inject({url,cookies:cookies[0]})).json().supported,false);
 }finally{await current.app.close()}
});
test('QQ favorite downloader rejects userinfo, unrelated domains, local hosts and non-HTTPS',async()=>{
 for(const url of ['http://gchat.qpic.cn/a','https://127.0.0.1/a','https://qpic.cn.evil.test/a','https://user@qpic.cn/a','https://qpic.cn:1234/a'])await assert.rejects(downloadFavorite(url,new AbortController().signal));
});
test('closing API aborts in-flight favorite import without publishing a partial library',async()=>{
 const root=await mkdtemp(path.resolve('.local/favorite-close-')),app=Fastify();let entered!:()=>void;const barrier=new Promise<void>(r=>entered=r);
 registerQQFavorites(app,root,async()=>({id:'a',protocol:'napcat',qq_uin:'90000',generation:1,desired_state:'running',health:{protocol:{online:true}}}),{call:async(_r,_b,action)=>action==='get_login_info'?{user_id:'90000'}:[{md5:'pending',url:'https://gchat.qpic.cn/pending'}],download:async(_url,signal)=>{entered();return new Promise((_resolve,reject)=>{if(signal.aborted)reject(new Error('aborted'));else signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true})})}});
 assert.equal((await app.inject({url:'/v1/bots/a/qq-favorites/sync',method:'POST'})).statusCode,202);await barrier;await app.close();await assert.rejects(readFile(path.join(root,'bots/a/qq-favorites/index.json')));
});
