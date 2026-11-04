import test from 'node:test';import assert from 'node:assert/strict';import {execFile} from 'node:child_process';import path from 'node:path';
test('real social tool/storage contracts: readback, GIF, quote scopes, pagination and query reuse',{timeout:30000},async()=>{
 const output=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-X','utf8','-B','tests/social_protocol_contract_test.py'],{windowsHide:true,timeout:25000},(error,stdout,stderr)=>error?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(output,/Ran 25 tests/);assert.match(output,/OK/);
});
