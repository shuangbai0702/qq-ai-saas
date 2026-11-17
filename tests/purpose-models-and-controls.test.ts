import test from 'node:test';
import assert from 'node:assert/strict';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
import {delegatedCapabilityEnabled} from '../apps/node-agent/src/dsh-host.js';
test('purpose overrides normalize without changing main/default routes',()=>{
 for(const field of ['delegationModel','summaryModel','favoriteModel','auxiliaryModel']){
  const config=BotConfigSchema.parse({...defaultConfig,[field]:' custom-model '});
  assert.equal((config as any)[field],'custom-model');assert.equal(config.model,defaultConfig.model);
  for(const value of ['',null,123,'x'.repeat(151)])assert(!BotConfigSchema.safeParse({...defaultConfig,[field]:value}).success);
 }
});
test('web switches narrow structured capabilities, never grant unknown tools',()=>{
 const entries={web_search:'webSearch',group_history:'groupHistory',execution_review:'executionReview',favorite_audit:'favoriteAudit'};
 for(const [capability,field] of Object.entries(entries)){
  assert(delegatedCapabilityEnabled(defaultConfig,capability));
  assert(!delegatedCapabilityEnabled({...defaultConfig,delegatedCapabilities:{...defaultConfig.delegatedCapabilities,[field]:false}},capability));
 }
 assert(!delegatedCapabilityEnabled(defaultConfig,'arbitrary_shell'));
 assert(delegatedCapabilityEnabled(defaultConfig,'bilibili')); // its original manifest remains authoritative
});
