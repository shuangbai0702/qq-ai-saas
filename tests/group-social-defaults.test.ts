import test from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {SocialControls} from '../apps/web/src/social-controls.js';
import assert from 'node:assert/strict';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
import {platformMemePolicy} from '../packages/contracts/fish-persona.js';
import {applyFishSocialPreset} from '../packages/contracts/fish-social-preset.js';
test('group social limits and platform images agree in schema, new bot and preset defaults',()=>{
 const schema=BotConfigSchema.parse({...defaultConfig,behavior:{}});
 for(const cfg of [schema,defaultConfig,applyFishSocialPreset({...defaultConfig,behavior:{...defaultConfig.behavior!,hourlyInterjectionLimit:1,memeEnabled:false,attentionEnabled:false}})]){
  assert.equal(cfg.behavior?.hourlyInterjectionLimit,20);
  assert.equal(cfg.behavior?.randomReplyProbability,.03);
  assert.equal(cfg.behavior?.attentionEnabled,true);
  assert.equal(cfg.behavior?.memeEnabled,true);
  assert.equal(Object.hasOwn(cfg.behavior!,'memeProbability'),false);
  assert.deepEqual(cfg.behavior?.memePacks,['blue-fat-fish']);
  assert.equal(Object.hasOwn(cfg.behavior!,'memeCooldownSeconds'),false);
  assert.equal(Object.hasOwn(cfg.behavior!,'dailyMemeLimit'),false);
 }
 assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{hourlyInterjectionLimit:21}}).success,false);
 assert.equal(BotConfigSchema.parse({...defaultConfig,behavior:{hourlyInterjectionLimit:0,memeEnabled:false}}).behavior?.hourlyInterjectionLimit,0);
});


test('default persona favors a relevant image when the platform offers candidates',()=>{assert.ok(defaultConfig.systemPrompt.includes(platformMemePolicy));assert.ok(!defaultConfig.systemPrompt.includes('日常纯文本为主'));});


test('legacy daily limits and probability are removed when loading or saving configuration',()=>{
 const parsed=BotConfigSchema.parse({...defaultConfig,behavior:{...defaultConfig.behavior,memeProbability:0,dailyMemeLimit:0,memeCooldownSeconds:3600}});
 assert.equal(parsed.behavior?.memeEnabled,true);assert.equal(Object.hasOwn(parsed.behavior!,'memeCooldownSeconds'),false);
 for(const key of ['memeProbability','dailyMemeLimit','memeCooldownSeconds'])assert.equal(Object.hasOwn(parsed.behavior!,key),false);
});


test('image settings keep availability diagnostics without a send quota or cooldown control',()=>{
 const html=renderToStaticMarkup(createElement(SocialControls,{config:defaultConfig,onChange:()=>{}}));
 assert.doesNotMatch(html,/同会话图片冷却|每天图片尝试上限/);
 assert.match(html,/最近发图状态/);
});
