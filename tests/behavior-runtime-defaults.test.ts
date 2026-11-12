import test from 'node:test';
import assert from 'node:assert/strict';
import runtimeDefaults from '../packages/contracts/behavior-runtime-defaults.json' with {type:'json'};
import {behaviorDefaults} from '../packages/contracts/behavior-defaults.js';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {AutonomyControls} from '../apps/web/src/autonomy-controls.js';
import {RhythmControls} from '../apps/web/src/plugin-controls.js';
import {readFile} from 'node:fs/promises';

test('reply pacing defaults off, remains configurable and accepts zero interval',()=>{
 const config=BotConfigSchema.parse({...defaultConfig,behavior:{}});
 assert.equal(config.behavior!.typingSimulation,false);
 assert.equal(config.behavior!.sendIntervalMs,0);
 const enabled=BotConfigSchema.parse({...config,behavior:{...config.behavior,typingSimulation:true,sendIntervalMs:800}});
 assert.equal(enabled.behavior!.typingSimulation,true);assert.equal(enabled.behavior!.sendIntervalMs,800);
 assert.equal(BotConfigSchema.safeParse({...config,behavior:{sendIntervalMs:-1}}).success,false);
 const html=renderToStaticMarkup(createElement(RhythmControls,{config,onChange:()=>{}}));
 assert.match(html,/aria-label="自然打字停顿"/);assert.match(html,/aria-label="气泡发送间隔"/);
 for(const label of ['自然打字停顿','气泡发送间隔']){
  const input=html.match(new RegExp('<input[^>]*aria-label="'+label+'"[^>]*>'))?.[0];
  assert.ok(input);assert.doesNotMatch(input,/checked=/);
 }
});

test('group, owner and quiet defaults share the runtime contract and preserve explicit overrides',()=>{
 const parsed=BotConfigSchema.parse({...defaultConfig,behavior:{}}).behavior!;
 for(const key of ['groupParticipationEnabled','ownerContactEnabled','quietStart','quietEnd'] as const){
  assert.equal(behaviorDefaults[key],runtimeDefaults[key]);assert.equal(parsed[key],runtimeDefaults[key]);
 }
 assert.deepEqual(runtimeDefaults,{groupParticipationEnabled:true,ownerContactEnabled:true,quietStart:1,quietEnd:7});
 const disabled=BotConfigSchema.parse({...defaultConfig,proactive:false,behavior:{groupParticipationEnabled:false,ownerContactEnabled:false,quietStart:0,quietEnd:0}});
 assert.equal(disabled.proactive,false);assert.equal(disabled.behavior!.groupParticipationEnabled,false);
 assert.equal(disabled.behavior!.ownerContactEnabled,false);assert.equal(disabled.behavior!.quietStart,0);assert.equal(disabled.behavior!.quietEnd,0);
});

test('owner contact UI remains enabled independently of proactive and explains durable task authority',async()=>{
 const config=BotConfigSchema.parse({...defaultConfig,proactive:false,behavior:{ownerContactEnabled:true,ownerUin:'12345678'}});
 const html=renderToStaticMarkup(createElement(AutonomyControls,{config,onChange:()=>{},kind:'owner'}));
 assert.match(html,/主人联系由自己的开关控制/);assert.match(html,/保留用户已授权的持久提醒/);
 assert.doesNotMatch(html,/暂不执行/);assert.doesNotMatch(html,/定时主动联系总开关/);
 const taskUi=await readFile(new URL('../apps/web/src/proactive-controls.tsx',import.meta.url),'utf8');
 assert.match(taskUi,/不会周期性调用模型规划/);assert.match(taskUi,/用户已经授权的提醒仍按原任务执行/);
 assert.doesNotMatch(taskUi,/自主层接管主人联系|主动层可根据真实兴趣形成目标/);
});


test('poke AI context defaults off, survives API parse and is exposed separately in UI',()=>{
 const config=BotConfigSchema.parse({...defaultConfig,behavior:{}});
 assert.equal(config.behavior!.pokeIntoAiContext,false);
 const enabled=BotConfigSchema.parse({...config,behavior:{...config.behavior,pokeIntoAiContext:true}});
 assert.equal(enabled.behavior!.pokeIntoAiContext,true);
 assert.equal(BotConfigSchema.parse(JSON.parse(JSON.stringify(enabled))).behavior!.pokeIntoAiContext,true);
 const html=renderToStaticMarkup(createElement(RhythmControls,{config,onChange:()=>{}}));
 const input=html.match(/<input[^>]*aria-label="允许戳一戳进入AI上下文"[^>]*>/)?.[0];
 assert.ok(input);assert.doesNotMatch(input,/checked=/);
});
