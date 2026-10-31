import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
test('bounded companion defaults are enabled; planner is bounded and bounds stay enforced',()=>{
 const b=BotConfigSchema.parse(defaultConfig).behavior!;assert.equal(b.attentionEnabled,true);assert.equal(b.autonomousGroupContact,true);assert.equal(b.selfDevelopment,true);assert.equal(b.memorySemantic,true);assert.equal(BotConfigSchema.parse({...defaultConfig,behavior:{attentionEnabled:false}}).behavior!.attentionEnabled,false);
 for(const key of ['autoSignature','memeSemantic'] as const)assert.equal(b[key],false);
 for(const behavior of [{dailyAutoSignatureLimit:3},{dailyPlanLimit:101},{fatigueMaxReplies:21},{embeddingBaseUrl:'https://user:secret@example.com/v1'}])assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{...b,...behavior}}).success,false);
});
test('Windows tuning is the standard for new bots, missing fields and presets; explicit values survive parsing',()=>{
 const keys=['memeProbability','memeCooldownSeconds','dailyMemeLimit','randomReplyProbability','fatigueEnabled','fatigueWindowSeconds','fatigueMaxReplies'] as const;
 const expected=[.7,20,50,.03,true,300,4];
 for(const config of [defaultConfig,BotConfigSchema.parse({...defaultConfig,behavior:{}})])assert.deepEqual(keys.map(k=>config.behavior![k]),expected);
 const custom={memeProbability:.2,memeCooldownSeconds:120,dailyMemeLimit:3,randomReplyProbability:0,fatigueEnabled:false,fatigueWindowSeconds:600,fatigueMaxReplies:2};
 const parsed=BotConfigSchema.parse({...defaultConfig,behavior:custom});
 assert.deepEqual(keys.map(k=>parsed.behavior![k]),keys.map(k=>custom[k]));
});
test('model schedule, lifecycle, automatic signature and profile tools',{timeout:30000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/companion_features_test.py'],{windowsHide:true,timeout:25000},(err,stdout,stderr)=>err?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/OK/);
});
