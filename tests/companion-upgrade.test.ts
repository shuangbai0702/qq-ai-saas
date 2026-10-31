import test from 'node:test';import assert from 'node:assert/strict';import {execFile} from 'node:child_process';import {promisify} from 'node:util';import path from 'node:path';
test('companion integration: one reflection, actual recipient, review races and expression variety',async()=>{
 const r=await promisify(execFile)(path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python')),['-B','tests/companion_upgrade_test.py'],{windowsHide:true,timeout:30000});
 assert.match(r.stderr,/Ran 11 tests/);assert.match(r.stderr,/\bOK\b/);
});
