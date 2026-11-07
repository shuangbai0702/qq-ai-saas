import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile} from 'node:fs/promises';import path from 'node:path';import {execFileSync} from 'node:child_process';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';import {defaultConfig,BotConfigSchema,applyFishSocialPreset} from '../packages/contracts/index.js';
const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
test('fish preset applies source rules and preserves customer model, identity and recipients',()=>{
 const original=BotConfigSchema.parse({...defaultConfig,model:'customer-model',systemPrompt:'客户自己的角色',baseUrl:'https://example.org/v1',behavior:{...defaultConfig.behavior,ownerUin:'123456',proactiveTargets:['private:987654'],dailyProactiveLimit:2}}),result=applyFishSocialPreset(original);
 assert.equal(result.model,original.model);assert.equal(result.systemPrompt,original.systemPrompt);assert.equal(result.baseUrl,original.baseUrl);assert.equal(result.behavior?.ownerUin,'123456');assert.deepEqual(result.behavior?.proactiveTargets,['private:987654']);assert.equal(result.behavior?.dailyProactiveLimit,2);
 assert.equal(result.historyTurns,35);assert.equal(result.behavior?.randomReplyProbability,.03);assert.equal(result.behavior?.followupSeconds,120);assert.equal(result.behavior?.followupReplyProbability,.8);assert.equal(Object.hasOwn(result.behavior!,'memeProbability'),false);assert.equal(result.behavior?.ambientContextMessages,50);assert.equal(result.behavior?.stickerAllGroups,true);
 assert.equal(result.behavior?.styleLearning,true);assert.equal(result.behavior?.memoryAutoCapture,true);assert.deepEqual(result.behavior?.memePacks,['blue-fat-fish']);assert.equal(result.behavior?.galleryEnabled,false);
 assert.equal(original.behavior?.ownerUin,'123456');
});
test('installed animated pack, explicit image request, probability and group collection are actual runtime mechanisms',()=>{
 execFileSync(python,['tests/fish_alignment_test.py'],{windowsHide:true,stdio:'pipe'});
});
test('pack thumbnails and immediate exclusions have tenant authorization and revision isolation',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/fish-pack-')),agent:false});
 try{
  const cookies:Array<{fish_session:string}>=[];const bots:string[]=[];for(const name of ['a','b']){const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:name+'@fish-pack.test',name,password:'fish-pack-test-123'}});cookies.push({fish_session:r.cookies[0].value});bots.push((await current.app.inject({method:'POST',url:'/v1/bots',cookies:cookies.at(-1),payload:{name:'图包'+name}})).json().id)}
  const url='/v1/bots/'+bots[0]+'/meme-packs',get=()=>current.app.inject({url,cookies:cookies[0]});const pack=(await get()).json().packs[0];assert.equal(pack.installed,true);assert.equal(pack.items.length,157);
  assert.equal((await current.app.inject({url})).statusCode,401);assert.equal((await current.app.inject({url,cookies:cookies[1]})).statusCode,404);
  const item=pack.items[0],imageUrl=url+'/blue-fat-fish/'+item.id,payload={enabled:false,expectedVersion:pack.version};
  const media=await current.app.inject({url:imageUrl,cookies:cookies[0]});assert.equal(media.statusCode,200);assert.match(media.headers['content-type'] as string,/image\/gif/);assert.ok(media.rawPayload.length<1048576);
  assert.equal((await current.app.inject({url:imageUrl,cookies:cookies[1]})).statusCode,404);assert.equal((await current.app.inject({url:imageUrl,method:'PATCH',cookies:cookies[1],payload})).statusCode,404);
  assert.equal((await current.app.inject({url:imageUrl,method:'PATCH',cookies:cookies[0],payload})).statusCode,200);assert.equal((await current.app.inject({url:imageUrl,method:'PATCH',cookies:cookies[0],payload})).statusCode,409);
  assert.equal((await get()).json().packs[0].items[0].enabled,false);assert.equal((await current.app.inject({url:'/v1/bots/'+bots[1]+'/meme-packs',cookies:cookies[1]})).json().packs[0].items[0].enabled,true);
  const root=path.join(current.root,'bots',bots[0]);const catalog=JSON.parse(execFileSync(python,['-c',"import sys,json;sys.path.insert(0,'adapters/astrbot');from meme_packs import catalog;print(json.dumps(catalog(sys.argv[1],['blue-fat-fish'])))",root],{encoding:'utf8',windowsHide:true}));assert.ok(!catalog.some((x:any)=>x.id===item.id));assert.equal(catalog.length,156);
  assert.deepEqual(JSON.parse(await readFile(path.join(root,'meme-pack-settings.json'),'utf8')).disabled,[item.id]);
 }finally{await current.app.close()}
});
