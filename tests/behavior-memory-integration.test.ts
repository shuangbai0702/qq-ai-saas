import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';
test('production memory policy ownership, platform fallback, ambient isolation and output cleaning',{timeout:30000},async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const output=await new Promise<string>((resolve,reject)=>execFile(python,['tests/behavior_memory_integration_test.py'],{windowsHide:true},(error,stdout,stderr)=>error?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(output,/Ran 21 tests/);assert.match(output,/OK/);
});
