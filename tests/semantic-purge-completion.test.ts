import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';

test('semantic purge completes atomically and does not repeat retired cleanup',{timeout:20000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/semantic_purge_completion_test.py'],{windowsHide:true,timeout:15000},(err,stdout,stderr)=>err?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/OK/);
});
