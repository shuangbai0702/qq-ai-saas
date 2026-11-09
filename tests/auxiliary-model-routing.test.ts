import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';

test('auxiliary model config is optional, validated and preserves main model',()=>{
  assert.equal(defaultConfig.auxiliaryModel,undefined);
  const config=BotConfigSchema.parse({...defaultConfig,auxiliaryModel:'  aux-model  '});
  assert.equal(config.auxiliaryModel,'aux-model');
  assert.equal(config.model,defaultConfig.model);
  for(const auxiliaryModel of ['', ' ', 'x'.repeat(151),123,null])assert.equal(BotConfigSchema.safeParse({...defaultConfig,auxiliaryModel}).success,false);
});
test('trusted auxiliary routing: SDK model, concurrency, final merge and wire evidence',{timeout:30000},async()=>{
  const output=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-X','utf8','-B','tests/auxiliary_model_routing_test.py'],{windowsHide:true,timeout:25000},(error,stdout,stderr)=>error?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
  assert.match(output,/Ran 5 tests/);assert.match(output,/OK/);
});
