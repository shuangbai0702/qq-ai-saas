import test from 'node:test';
import assert from 'node:assert/strict';
import {BotConfigSchema,defaultConfig,behaviorDefaults} from '../packages/contracts/index.js';
import {autonomousDate} from '../apps/web/src/autonomous-status.js';
test('omitted and empty behavior share canonical defaults; explicit customer values survive',()=>{
 const {behavior,...base}=defaultConfig;
 assert.deepEqual(BotConfigSchema.parse(base).behavior,behaviorDefaults);
 assert.deepEqual(BotConfigSchema.parse({...base,behavior:{}}).behavior,behaviorDefaults);
 const saved=BotConfigSchema.parse({...base,behavior:{enabled:false,memory:false,burstDebounceMs:0,groupParticipationGroups:[],ownerUin:'12345678',ownerContactIntervalMinutes:80,dailySummaryLimit:0}}).behavior;
 assert.equal(saved.enabled,false);assert.equal(saved.memory,false);assert.equal(saved.burstDebounceMs,0);assert.deepEqual(saved.groupParticipationGroups,[]);assert.equal(saved.ownerUin,'12345678');assert.equal(saved.ownerContactIntervalMinutes,80);assert.equal(Object.hasOwn(saved,'dailySummaryLimit'),false);
});
test('autonomy dates accept actual persisted milliseconds and ISO dates without inventing unknown values',()=>{
 const at=1793646000000;assert.equal(autonomousDate(at),autonomousDate(new Date(at).toISOString()));assert.equal(autonomousDate(undefined),'暂无');assert.equal(autonomousDate('bad'),'时间未知');
});
