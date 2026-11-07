import test from 'node:test';import assert from 'node:assert/strict';import {execFile} from 'node:child_process';import path from 'node:path';
import {BotConfigSchema,defaultConfig,applyFishSocialPreset} from '../packages/contracts/index.js';
test('plugin defaults are bounded and recommended settings preserve owner, key-independent model and favorites policy',()=>{
 const cfg=applyFishSocialPreset(BotConfigSchema.parse({...defaultConfig,model:'private-model',behavior:{...defaultConfig.behavior,ownerUin:'12345678',proactiveTargets:['private:87654321']}}));
 assert.equal(cfg.model,'private-model');assert.equal(cfg.behavior?.ownerUin,'12345678');assert.deepEqual(cfg.behavior?.proactiveTargets,['private:87654321']);assert.equal(cfg.behavior?.typingSimulation,true);assert.equal(cfg.behavior?.typingSpeed,15);assert.equal(cfg.behavior?.memorySummary,true);assert.equal(cfg.behavior?.summaryRounds,35);assert.equal(cfg.behavior?.pokeProbability,.25);assert.equal(cfg.behavior?.qqProfile,true);
 assert.equal(BotConfigSchema.safeParse({...cfg,behavior:{...cfg.behavior,typingSpeed:0}}).success,false);assert.equal('dailySummaryLimit' in BotConfigSchema.parse({...cfg,behavior:{...cfg.behavior,dailySummaryLimit:999}}).behavior!,false,'removed daily quota cannot disable future reflection');
});
test('controlled plugins: reflection persistence, stale completion, owner tools and native SDK pacing',{timeout:60000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['tests/plugin_alignment_test.py'],{windowsHide:true,timeout:55000},(err,stdout,stderr)=>err?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));assert.match(out,/OK/);
});
