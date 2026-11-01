import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import {BotConfigSchema,defaultConfig,applyFishSocialPreset} from '../packages/contracts/index.js';

test('autonomy scopes, conversation clocks, owner decisions and stale delivery',{timeout:60000},async()=>{
 const b=BotConfigSchema.parse({...defaultConfig,behavior:{}}).behavior!;
 assert.equal(b.groupParticipationIntervalMinutes,15);assert.deepEqual(b.groupParticipationGroups,[]);assert.equal(b.ownerContactIntervalMinutes,60);
 assert.equal(b.groupDecisionTimeoutSeconds,10);assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{groupDecisionTimeoutSeconds:16}}).success,false);
 assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{groupParticipationIntervalMinutes:14}}).success,false);
 assert.equal(applyFishSocialPreset(defaultConfig).behavior?.ownerContactEnabled,true);
 const result=await promisify(execFile)(path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python')),['-B','tests/autonomy_test.py'],{windowsHide:true,timeout:40000,maxBuffer:1024*1024});
 assert.match(result.stderr,/Ran 39 tests/);assert.match(result.stderr,/\bOK\b/);
});
