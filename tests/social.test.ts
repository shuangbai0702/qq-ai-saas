import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import path from 'node:path';import {execFile} from 'node:child_process';import {mkdtemp,readFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';
test('native send completion cannot recreate a stopped or replaced Bot store',async()=>{const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['tests/send_lifecycle_test.py'],{windowsHide:true,timeout:20000},(e,stdout,stderr)=>e?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));assert.match(out,/PASS protocol send late completion/)});
test('social learning evidence, scope, bounded images and stale/cancelled tasks',async()=>{const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['tests/social_test.py'],{windowsHide:true,timeout:30000},(e,stdout,stderr)=>e?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));assert.match(out,/Ran 38 tests/);assert.match(out,/OK/)});
test('reaction API: ownership, PNG validation, persistence, isolation and delete',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/social-api-')),agent:false});
 try{const cookies:Array<{fish_session:string}>=[];for(const user of ['a','b']){const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:user+'@social.test',name:user,password:'social-test-123'}});cookies.push({fish_session:r.cookies[0].value})}
 const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies:cookies[0],payload:{name:'图片隔离'}});const id=created.json().id;const url='/v1/bots/'+id+'/reactions';
 const png=(await readFile('templates/reactions/laugh.png')).toString('base64');const added=await current.app.inject({method:'POST',url,cookies:cookies[0],payload:{png,meaning:'开玩笑时笑哭'}});assert.equal(added.statusCode,200,added.body);const image=added.json().id;
 for(const method of ['GET','DELETE']as const)assert.equal((await current.app.inject({method,url:url+'/'+image,cookies:cookies[1]})).statusCode,404);
 assert.equal((await current.app.inject({method:'GET',url,cookies:cookies[1]})).statusCode,404);
 assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[1],payload:{png,meaning:'越权上传'}})).statusCode,404);
 assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[0],payload:{png:Buffer.from('fake').toString('base64'),meaning:'伪造PNG'}})).statusCode,400);
 assert.equal((await current.app.inject({method:'GET',url:url+'/'+image,cookies:cookies[0]})).headers['content-type'],'image/png');
 const list=await current.app.inject({method:'GET',url,cookies:cookies[0]});assert.deepEqual(list.json().items.map((x:any)=>x.id),[image]);assert.ok(!list.body.includes('path'));
 for(const retired of ['hug','laugh','thinking'])for(const method of ['GET','PATCH','DELETE']as const){const r=await current.app.inject({method,url:url+'/'+retired,cookies:cookies[0],...(method==='PATCH'?{payload:{expectedVersion:'0'.repeat(64),meaning:'禁止重新启用旧图',avoid:'',enabled:true}}:{})});assert.equal(r.statusCode,404,'retired IDs cannot be read or re-enabled')}
 for(let round=0;round<6;round++){
  const concurrent=await Promise.all([...Array.from({length:6},()=>current.app.inject({method:'GET',url,cookies:cookies[0]})),current.app.inject({method:'DELETE',url:url+'/'+image,cookies:cookies[0]})]);
  assert.ok(concurrent.every(r=>r.statusCode===200),'list/delete concurrency must not expose missing-file 500');
  const restored=await current.app.inject({method:'POST',url,cookies:cookies[0],payload:{png,meaning:'开玩笑时笑哭'}});assert.equal(restored.statusCode,200,restored.body);
 }
 assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[0],payload:{png,meaning:'开玩笑时笑哭'}})).statusCode,200);
 assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[0],payload:{png,meaning:'开玩笑时笑哭'}})).statusCode,429,'upload request limit remains enforced');
 await current.app.inject({method:'DELETE',url:url+'/'+image,cookies:cookies[0]});assert.equal((await current.app.inject({method:'GET',url:url+'/'+image,cookies:cookies[0]})).statusCode,404);
 }finally{await current.app.close()}
});
