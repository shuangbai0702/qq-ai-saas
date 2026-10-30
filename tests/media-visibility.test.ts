import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import {execFileSync} from 'node:child_process';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
import {defaultConfig,BotConfigSchema} from '../packages/contracts/index.js';
test('new bot social defaults are active while explicitly disabled existing configuration is preserved',()=>{
 assert.equal(defaultConfig.behavior?.styleLearning,true);assert.equal(defaultConfig.behavior?.memeEnabled,true);assert.equal(defaultConfig.behavior?.expressionSharing,'bot');assert.equal(defaultConfig.behavior?.splitMessages,true);assert.equal(defaultConfig.behavior?.randomReplyProbability,0.02);assert.deepEqual(defaultConfig.behavior?.proactiveTargets,[]);
 const old=BotConfigSchema.parse({...defaultConfig,proactive:false,behavior:{styleLearning:false,memeEnabled:false,expressionSharing:'local'}});assert.equal(old.behavior?.styleLearning,false);assert.equal(old.behavior?.memeEnabled,false);assert.equal(old.proactive,false);
});
test('expression sharing only links explicitly configured groups, keeps facts and bot boundaries',()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 execFileSync(python,['tests/expression_sharing_test.py'],{windowsHide:true,stdio:'pipe'});
});
test('all built-in reaction images are visible and per-bot overrides govern actual runtime catalog',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/media-visibility-')),agent:false});
 try{
  const cookies=[];for(const name of ['a','b']){const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:name+'@visibility.test',name,password:'visibility-test-123'}});cookies.push({fish_session:r.cookies[0].value})}
  const id=(await current.app.inject({method:'POST',url:'/v1/bots',cookies:cookies[0],payload:{name:'可见内置图'}})).json().id,url='/v1/bots/'+id+'/reactions';
  const list=(await current.app.inject({url,cookies:cookies[0]})).json();assert.deepEqual(list.items.map((x:any)=>x.id),['hug','laugh','thinking']);assert.ok(list.items.every((x:any)=>x.builtin));assert.equal(list.limit,5);
  assert.equal((await current.app.inject({url:url+'/hug',cookies:cookies[0]})).headers['content-type'],'image/png');assert.equal((await current.app.inject({url:url+'/hug',cookies:cookies[1]})).statusCode,404);
  const item=list.items[0],payload={meaning:'支持与亲近',avoid:'严肃事务不用',enabled:false,expectedVersion:item.version};
  assert.equal((await current.app.inject({url:url+'/hug',method:'PATCH',cookies:cookies[1],payload})).statusCode,404);
  assert.equal((await current.app.inject({url:url+'/hug',method:'PATCH',cookies:cookies[0],payload})).statusCode,200);
  assert.equal((await current.app.inject({url:url+'/hug',method:'PATCH',cookies:cookies[0],payload})).statusCode,409);
  const next=(await current.app.inject({url,cookies:cookies[0]})).json().items[0];assert.equal(next.enabled,false);
  const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
  const catalog=()=>JSON.parse(execFileSync(python,['-c',"import sys,sqlite3,json;from pathlib import Path;sys.path.insert(0,'adapters/astrbot');from social import SocialStore;s=SocialStore(sqlite3.connect(':memory:'),Path(sys.argv[1]));print(json.dumps(s.catalog('private:1')));s.close()",path.join(current.root,'bots',id)],{windowsHide:true,encoding:'utf8'}));
  assert.ok(!catalog().some((x:any)=>x.id==='hug'),'disabled built-in absent from actual reply candidates');
  assert.equal((await current.app.inject({url:url+'/hug',method:'PATCH',cookies:cookies[0],payload:{...payload,enabled:true,expectedVersion:next.version}})).statusCode,200);
  assert.equal(catalog().find((x:any)=>x.id==='hug').meaning,'支持与亲近');
  assert.equal((await current.app.inject({url:'/v1/bots/'+id+'/gallery/hug',cookies:cookies[0]})).statusCode,404);
 }finally{await current.app.close()}
});
