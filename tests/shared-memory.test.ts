import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';

test('same QQ shares attributed long-term memory across conversations with deletion and lifecycle fences',{timeout:30000},async()=>{
 const output=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/shared_memory_test.py'],{windowsHide:true,timeout:25000},(error,stdout,stderr)=>error?reject(new Error(`${error}\n${stdout}\n${stderr}`)):resolve(stdout+stderr)));
 assert.match(output,/Ran 13 tests/);assert.match(output,/OK/);
});
