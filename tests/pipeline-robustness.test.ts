import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';

test('pipeline receipts, stale interruption, context failure and selected-work cleanup',{timeout:30000},async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const result=await promisify(execFile)(python,['-B','tests/pipeline_boundaries_test.py'],{windowsHide:true,timeout:25000,maxBuffer:1024*1024});
 // Voice, pipeline and old-memory output boundaries must all run.
 assert.equal((result.stdout.match(/^PASS /gm)||[]).length,10,result.stdout+result.stderr);
});
