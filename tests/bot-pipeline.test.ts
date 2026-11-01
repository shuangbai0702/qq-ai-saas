import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';

test('pipeline stage ownership, restart outcomes and deferred group expiry',{timeout:45000},async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 for(const [file,count] of [['bot_pipeline_test.py',7],['deferred_group_test.py',6]] as const){
  const result=await promisify(execFile)(python,['-B',`tests/${file}`],{windowsHide:true,timeout:15000,maxBuffer:1024*1024});
  assert.match(result.stderr,new RegExp(`Ran ${count} tests`));assert.match(result.stderr,/\bOK\b/);
 }
});
