import test from 'node:test';import assert from 'node:assert/strict';import {runtimeStatus} from '../packages/shared/runtime-status.js';
import {api,ApiError} from '../apps/web/src/api.js';import {defaultConfig} from '../packages/contracts/index.js';
test('health freshness rejects stale, future, missing and cleared runtime flags',()=>{
 const now=Date.now(),bot={desired_state:'running',health:{sampledAt:new Date(now).toISOString(),protocol:{online:true},ai:{ready:true,alive:true}}};assert.equal(runtimeStatus(bot,now).online,true);
 for(const sampledAt of [new Date(now-90001).toISOString(),new Date(now+5001).toISOString(),''])assert.equal(runtimeStatus({...bot,health:{...bot.health,sampledAt}},now).online,false);
 assert.equal(runtimeStatus({...bot,desired_state:'offline'},now).aiReady,false);assert.equal(runtimeStatus({desired_state:'running',health:{}},now).online,false);
 assert.equal(defaultConfig.behavior?.qqFaces,false,'new users must not emit native QQ faces');
});
test('HTTP errors are actionable, mutation failures are not retried, caller cancellation stays cancellation',async()=>{
 const original=globalThis.fetch;try{
  globalThis.fetch=async()=>new Response('<html>Bad Gateway</html>',{status:502});await assert.rejects(api('/bots'),/服务暂时不可用/);
  globalThis.fetch=async()=>Response.json({error:'rate limited'},{status:429,headers:{'Retry-After':'12'}});await assert.rejects(api('/bots'),/12 秒/);
  globalThis.fetch=async()=>Response.json({error:'请先登录'},{status:401});await assert.rejects(api('/bots'),/备份未保存/);
  globalThis.fetch=async()=>Response.json({error:'邮箱或密码不正确'},{status:401});await assert.rejects(api('/auth/login',{}),/邮箱或密码不正确/);
  let attempts=0;globalThis.fetch=async()=>{attempts++;throw new TypeError('Failed to fetch')};await assert.rejects(api('/bots',{}),/可能已提交/);assert.equal(attempts,1);
  const controller=new AbortController();controller.abort();globalThis.fetch=async()=>{throw controller.signal.reason};await assert.rejects(api('/bots',undefined,undefined,controller.signal),e=>e===controller.signal.reason);
 }finally{globalThis.fetch=original}
});

import {disableNativeQQFaces} from '../packages/shared/native-face-policy.js';
test('native-face policy preserves custom persona, memories/media switches and original object',()=>{
 const config={systemPrompt:'自定义角色，保持原样。',provider:'custom',behavior:{qqFaces:true,memeEnabled:true,stickerCollect:true,galleryEnabled:true,ownerUin:'90123456',memory:true},extra:'preserve'};
 const changed=disableNativeQQFaces(config);assert.equal(changed.behavior.qqFaces,false);assert.equal(config.behavior.qqFaces,true);assert.equal(changed.behavior.memeEnabled,true);assert.equal(changed.behavior.stickerCollect,true);assert.equal(changed.behavior.galleryEnabled,true);assert.equal(changed.extra,'preserve');assert.ok(changed.systemPrompt.startsWith(config.systemPrompt));assert.ok(changed.systemPrompt.includes('[闲聊表达]'));assert.ok(defaultConfig.systemPrompt.includes('[闲聊表达]'));assert.deepEqual(disableNativeQQFaces(changed),changed,'repeated rollout must be idempotent');const updated=disableNativeQQFaces({...config,systemPrompt:config.systemPrompt+'\n[闲聊表达]旧的托管规则\n保留自定义后文。'});assert.ok(!updated.systemPrompt.includes('旧的托管规则'));assert.ok(updated.systemPrompt.includes('保留自定义后文。'));assert.equal(updated.systemPrompt.match(/\[闲聊表达\]/g)?.length,1);
});
