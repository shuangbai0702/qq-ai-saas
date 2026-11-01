import test from 'node:test';import assert from 'node:assert/strict';import {execFile} from 'node:child_process';import path from 'node:path';
test('uncompressed cross-session continuity and owner-scoped group actions survive summary and lifecycle boundaries',{timeout:40000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/conversation_continuity_test.py'],{windowsHide:true,timeout:35000},(error,stdout,stderr)=>error?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/Ran 13 tests/);assert.match(out,/OK/);
});
