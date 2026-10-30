import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
test('configuration semantics: probability windows, quota, restart, target selection and relearning',async()=>{
 const result=await promisify(execFile)(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['tests/config_audit_test.py'],{windowsHide:true});
 assert.match(result.stderr,/Ran 9 tests/);assert.match(result.stderr,/OK/);
});
