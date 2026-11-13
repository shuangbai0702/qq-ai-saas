import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';

test('semantic retirement preserves event loop and transaction ownership',{timeout:60000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/semantic_async_test.py'],{windowsHide:true,timeout:55000},(err,stdout,stderr)=>err?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/OK/);
});
