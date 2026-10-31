import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import {BotConfigSchema,defaultConfig,applyFishSocialPreset} from '../packages/contracts/index.js';

test('natural conversation defaults, source evidence, retrieval and async deletion safety',async()=>{
 const b=BotConfigSchema.parse({...defaultConfig,behavior:{}}).behavior!;
 assert.equal(b.naturalConversation,true);assert.equal(b.followupSeconds,120);
 assert.equal(applyFishSocialPreset(defaultConfig).behavior?.naturalConversation,true);
 assert.equal(BotConfigSchema.parse({...defaultConfig,behavior:{naturalConversation:false,followupSeconds:0}}).behavior?.naturalConversation,false);
 const r=await promisify(execFile)(path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python')),['-B','tests/natural_conversation_test.py'],{windowsHide:true,timeout:30000});
 assert.match(r.stderr,/Ran 15 tests/);assert.match(r.stderr,/\bOK\b/);
});
