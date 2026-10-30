import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp,readFile} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
const exec=promisify(execFile),python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);

test('behavior management: runtime reads, tombstones, epochs and delayed learning',async()=>{
 const result=await exec(python,['tests/capability_data_test.py'],{windowsHide:true});assert.match(result.stderr,/Ran 9 tests/);assert.match(result.stderr,/OK/);
});

test('capability data and gallery management authenticate, isolate, persist and reject stale changes',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/capability-api-')),agent:false});
 try{
  const cookies=[];for(const name of ['a','b']){
   const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:`${name}@capabilities.test`,password:'capability-test-123',name}});assert.equal(r.statusCode,200,r.body);cookies.push({fish_session:r.cookies[0].value});
  }
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies:cookies[0],payload:{name:'管理测试'}});const id=created.json().id;
  const url=`/v1/bots/${id}/behavior-data`;
  assert.equal((await current.app.inject({url})).statusCode,401);
  assert.equal((await current.app.inject({url,cookies:cookies[0]})).json().initialized,false);
  await exec(python,['tests/capability_data_test.py','--seed',path.join(current.root,'bots',id,'behavior.sqlite')],{windowsHide:true});
  const list=await current.app.inject({url,cookies:cookies[0]});assert.equal(list.statusCode,200,list.body);assert.equal(list.json().facts.length,2);
  assert.equal((await current.app.inject({url,cookies:cookies[1]})).statusCode,404);
  const body={action:'edit_fact',scope:'group:12345',subject:'123456',name:'饮料',expectedVersion:1,value:'乌龙茶'};
  assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[1],payload:body})).statusCode,404);
  assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[0],payload:body})).statusCode,200);
  assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[0],payload:body})).statusCode,409);
  const filtered=await current.app.inject({url:url+'?scope=group:12345&subject=123456&query='+encodeURIComponent('乌龙'),cookies:cookies[0]});assert.equal(filtered.json().facts.length,1);
  assert.equal((await current.app.inject({url:url+'?scope=../../other',cookies:cookies[0]})).statusCode,400);
  assert.equal((await current.app.inject({method:'POST',url,cookies:cookies[0],payload:{action:'sql',sql:'SELECT * FROM secrets'}})).statusCode,400);
  const gallery=`/v1/bots/${id}/reactions`,png=(await readFile('templates/reactions/laugh.png')).toString('base64');
  const image=(await current.app.inject({method:'POST',url:gallery,cookies:cookies[0],payload:{png,meaning:'玩笑'}})).json().id;
  const item=(await current.app.inject({url:gallery,cookies:cookies[0]})).json().items[0];
  const patch={expectedVersion:item.version,meaning:'轻松的笑话',avoid:'难过与严肃场景',enabled:false};
  assert.equal((await current.app.inject({method:'PATCH',url:gallery+'/'+image,cookies:cookies[1],payload:patch})).statusCode,404);
  assert.equal((await current.app.inject({method:'PATCH',url:gallery+'/'+image,cookies:cookies[0],payload:patch})).statusCode,200);
  assert.equal((await current.app.inject({method:'PATCH',url:gallery+'/'+image,cookies:cookies[0],payload:patch})).statusCode,409);
  const saved=(await current.app.inject({url:gallery,cookies:cookies[0]})).json().items[0];assert.equal(saved.enabled,false);assert.equal(saved.avoid,patch.avoid);
 }finally{await current.app.close()}
});
