import test from 'node:test';
import assert from 'node:assert/strict';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';

test('group admission keeps the first two distinct configured groups',()=>{
 const parsed=BotConfigSchema.parse({...defaultConfig,behavior:{...defaultConfig.behavior,groupParticipationGroups:['11111','11111','22222','33333']}});
 assert.deepEqual(parsed.behavior.groupParticipationGroups,['11111','22222']);
 assert.deepEqual(defaultConfig.behavior.groupParticipationGroups,[]);
});
