import test from 'node:test';import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';import path from 'node:path';
test('execution memory transport and actual tool bindings preserve lifecycle and source identity',async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const r=await promisify(execFile)(python,['tests/execution_memory_test.py'],{windowsHide:true,timeout:25000});
 assert.match(r.stderr,/Ran 6 tests/);assert.match(r.stderr,/\bOK\b/);
});
