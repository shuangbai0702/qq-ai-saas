import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

for(const file of ['activity_main_journal_test.py','activity_round_checkpoint_test.py','model_request_budget_bridge_test.py']){
 test('B main Journal contract: '+file,{timeout:120000},async()=>{
  const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
  const result=await promisify(execFile)(python,['-X','utf8','-B','tests/'+file],{windowsHide:true,timeout:110000,maxBuffer:4*1024*1024});
  assert.match(result.stderr,/Ran \d+ tests?/);assert.match(result.stderr,/\bOK\b/);
 });
}
