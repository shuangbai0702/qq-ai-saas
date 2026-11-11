import test from 'node:test';import assert from 'node:assert/strict';import {execFile} from 'node:child_process';import {promisify} from 'node:util';import path from 'node:path';import {mkdtemp,readFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),exec=promisify(execFile);
test('chat sticker collector: opt-in, metadata, limits, late completion, scoped approval and separate gallery',async()=>{const r=await exec(python,['tests/media_separation_test.py'],{windowsHide:true});assert.match(r.stderr,/Ran 12 tests/);assert.match(r.stderr,/OK/)});
test('native SDK captures unmentioned stickers and routes only approved scoped images',async()=>{const r=await exec(python,['tests/sticker_native_test.py'],{windowsHide:true});assert.match(r.stdout,/PASS native unmentioned sticker capture/)});
test('separate gallery and sticker review use real ownership, version checks and persistence',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/media-separation-')),agent:false});
 try{
  const cookies=[];for(const email of ['a@media.test','b@media.test']){const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email,password:'media-test-123',name:email}});cookies.push({fish_session:r.cookies[0].value})}
  const id=(await current.app.inject({method:'POST',url:'/v1/bots',cookies:cookies[0],payload:{name:'媒体分类'}})).json().id,url='/v1/bots/'+id;
  assert.equal((await current.app.inject({url:url+'/stickers',cookies:cookies[0]})).json().items.length,0);
  await exec(python,['tests/media_separation_test.py','--seed',path.join(current.root,'bots',id,'behavior.sqlite')],{windowsHide:true});
  const list=await current.app.inject({url:url+'/stickers',cookies:cookies[0]});assert.equal(list.statusCode,200,list.body);assert.equal(list.json().items[0].status,'pending');
  for(const route of ['/stickers','/stickers/cap_0123456789abcdef','/gallery'])assert.equal((await current.app.inject({url:url+route,cookies:cookies[1]})).statusCode,404);
  assert.equal((await current.app.inject({url:url+'/stickers/cap_0123456789abcdef',cookies:cookies[0]})).headers['content-type'],'image/png');
  const review={status:'approved',meaning:'轻松玩笑',expectedVersion:1},endpoint=url+'/stickers/cap_0123456789abcdef';
  assert.equal((await current.app.inject({method:'PATCH',url:endpoint,cookies:cookies[1],payload:review})).statusCode,404);
  assert.equal((await current.app.inject({method:'PATCH',url:endpoint,cookies:cookies[0],payload:review})).statusCode,200);
  assert.equal((await current.app.inject({method:'PATCH',url:endpoint,cookies:cookies[0],payload:review})).statusCode,409);
  const png=(await readFile('templates/reactions/laugh.png')).toString('base64');const uploaded=await current.app.inject({method:'POST',url:url+'/gallery',cookies:cookies[0],payload:{png,meaning:'角色照片'}});assert.equal(uploaded.statusCode,200,uploaded.body);assert.match(uploaded.json().id,/^gal_/);
  assert.equal((await current.app.inject({url:url+'/reactions',cookies:cookies[0]})).json().items.filter((x:any)=>!x.builtin).length,0);assert.equal((await current.app.inject({url:url+'/gallery',cookies:cookies[0]})).json().items.length,1);
  const item=(await current.app.inject({url:url+'/gallery',cookies:cookies[0]})).json().items[0];assert.equal((await current.app.inject({method:'PATCH',url:url+'/gallery/'+item.id,cookies:cookies[0],payload:{expectedVersion:item.version,enabled:false,meaning:'角色照片',avoid:''}})).statusCode,200);
  assert.equal((await current.app.inject({method:'PATCH',url:endpoint,cookies:cookies[0],payload:{status:'rejected',expectedVersion:2}})).statusCode,200);
  assert.equal((await current.app.inject({url:endpoint,cookies:cookies[0]})).statusCode,404);
 }finally{await current.app.close()}
});
