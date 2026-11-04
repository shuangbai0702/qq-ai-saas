import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';

test('conversation provenance, cross-session receipts, summary and restart contracts',{timeout:30000},async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const result=await promisify(execFile)(python,['-X','utf8','-B','tests/message_provenance_test.py'],{windowsHide:true,timeout:25000,maxBuffer:1024*1024});
 assert.match(result.stderr,/Ran 13 tests/);assert.match(result.stderr,/\bOK\b/);
});
