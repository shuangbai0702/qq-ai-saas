import test from 'node:test';
import assert from 'node:assert/strict';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
import {platformMemePolicy} from '../packages/contracts/fish-persona.js';
import {applyFishSocialPreset} from '../packages/contracts/fish-social-preset.js';
test('group social limits and platform images agree in schema, new bot and preset defaults',()=>{
 const schema=BotConfigSchema.parse({...defaultConfig,behavior:{}});
 for(const cfg of [schema,defaultConfig,applyFishSocialPreset({...defaultConfig,behavior:{...defaultConfig.behavior!,hourlyInterjectionLimit:1,memeEnabled:false,attentionEnabled:false}})]){
  assert.equal(cfg.behavior?.hourlyInterjectionLimit,20);
  assert.equal(cfg.behavior?.randomReplyProbability,.03);
  assert.equal(cfg.behavior?.attentionEnabled,false);
  assert.equal(cfg.behavior?.memeEnabled,true);
  assert.equal(cfg.behavior?.memeProbability,.7);
  assert.deepEqual(cfg.behavior?.memePacks,['blue-fat-fish']);
  assert.equal(cfg.behavior?.memeCooldownSeconds,20);
  assert.equal(cfg.behavior?.dailyMemeLimit,50);
 }
 assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{hourlyInterjectionLimit:21}}).success,false);
 assert.equal(BotConfigSchema.parse({...defaultConfig,behavior:{hourlyInterjectionLimit:0,memeEnabled:false}}).behavior?.hourlyInterjectionLimit,0);
});


test('default persona favors a relevant image when the platform offers candidates',()=>{assert.ok(defaultConfig.systemPrompt.includes(platformMemePolicy));assert.ok(!defaultConfig.systemPrompt.includes('日常纯文本为主'));});
