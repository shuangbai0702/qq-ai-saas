import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';
import {BilibiliExecutor} from '../apps/node-agent/src/bilibili/executor.js';
import {BilibiliConfigSchema,biliOperations} from '../packages/contracts/bilibili.js';

test('Bilibili declarations depend on configured operations while live readiness never rewrites schemas',()=>{
 const executor=new BilibiliExecutor({} as any,'fixture',Buffer.alloc(32));
 const config=BilibiliConfigSchema.parse({enabled:true,scopes:['public.read','media.analyze','like.write']});
 const expected=executor.catalog({config});
 assert.ok(expected.some(op=>op.operation==='analyze'));
 assert.ok(expected.some(op=>op.operation==='like'));
 for(const account_state of [undefined,'ready','auth_required','expired']){
  for(const refresh_state of [undefined,'idle','pending','confirm_pending']){
   for(const media of [false,true]){
    executor.mediaReady=media;
    assert.deepEqual(executor.catalog({config,account_state,refresh_state,has_model_key:media}),expected);
   }
  }
 }
 assert.deepEqual(executor.catalog({config:{...config,enabled:false}}),[]);
 assert.ok(executor.catalog({config:{...config,scopes:['public.read']}}).every(op=>biliOperations[op.operation as keyof typeof biliOperations].scope==='public.read'));
});

test('real SDK declarations and execution guards remain stable across trusted event variants',async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const output=await new Promise<string>((resolve,reject)=>execFile(python,['tests/stable_tool_catalog_20261109_test.py'],{windowsHide:true},(error,stdout,stderr)=>error?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(output,/Ran 4 tests/);assert.match(output,/OK/);assert.match(output,/byte-identical across 24 event variants/);
});

test('periodic guidance follows confirmed business rounds and only appends its exact short text',async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const output=await new Promise<string>((resolve,reject)=>execFile(python,['tests/periodic_guidance_20261109_test.py'],{windowsHide:true},(error,stdout,stderr)=>error?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(output,/Ran 3 tests/);assert.match(output,/OK/);
});
