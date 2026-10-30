import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';

test('meme semantic ranking isolates approved catalogs, falls back and fences late work',{timeout:30000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/meme_semantic_test.py'],{windowsHide:true,timeout:25000},(err,stdout,stderr)=>err?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/OK/);
});
