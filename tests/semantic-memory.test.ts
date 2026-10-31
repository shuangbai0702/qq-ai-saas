import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
test('semantic memory defaults enabled and remote requests stay bounded',()=>{
 assert.equal(defaultConfig.behavior?.memorySemantic,true);
 for(const behavior of [{dailyEmbeddingLimit:1001},{embeddingModel:'x'.repeat(151)}])assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{...defaultConfig.behavior,...behavior}}).success,false);
 const parsed=BotConfigSchema.parse({...defaultConfig,behavior:{...defaultConfig.behavior,memorySemantic:true,embeddingModel:' BAAI/bge-m3 '}});
 assert.equal(parsed.behavior?.embeddingModel,'BAAI/bge-m3');
});
test('semantic recall isolates scope and provider, falls back and fences forgotten or stopped work',{timeout:20000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/semantic_memory_test.py'],{windowsHide:true,timeout:15000},(err,stdout,stderr)=>err?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/OK/);
});
