import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
test('companion controls remain opt-in and reject unbounded settings',()=>{
 const b=BotConfigSchema.parse(defaultConfig).behavior!;
 for(const key of ['autoSignature','proactiveModelSchedule','attentionEnabled','fatigueEnabled','memeSemantic'] as const)assert.equal(b[key],false);
 for(const behavior of [{dailyAutoSignatureLimit:3},{dailyPlanLimit:101},{fatigueMaxReplies:21},{embeddingBaseUrl:'https://user:secret@example.com/v1'}])assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{...b,...behavior}}).success,false);
});
test('model schedule, lifecycle, automatic signature and profile tools',{timeout:30000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/companion_features_test.py'],{windowsHide:true,timeout:25000},(err,stdout,stderr)=>err?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/OK/);
});
