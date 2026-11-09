import test from 'node:test';
import assert from 'node:assert/strict';
import runtimeDefaults from '../packages/contracts/behavior-runtime-defaults.json' with {type:'json'};
import {behaviorDefaults} from '../packages/contracts/behavior-defaults.js';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {AutonomyControls} from '../apps/web/src/autonomy-controls.js';
import {readFile} from 'node:fs/promises';

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
