import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';

test('hosted request keeps media and deferred-task instructions together without enabling passive handoffs',{timeout:30000},async()=>{
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
 const result=await promisify(execFile)(python,['-X','utf8','-B','tests/foreground_prompt_test.py'],{windowsHide:true,timeout:25000,maxBuffer:1024*1024});
 assert.match(result.stdout,/PASS actual hosted request preserves handoff and media prompts/);
});

test('self interest choices use actual reads, preserve persona, remain scoped and replay once',{timeout:30000},async()=>{
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
 const result=await promisify(execFile)(python,['-X','utf8','-B','tests/self_interest_test.py'],{windowsHide:true,timeout:25000,maxBuffer:1024*1024});
 assert.match(result.stderr,/Ran 6 tests/);assert.match(result.stderr,/\bOK\b/);
});
test('pinned native internal runner feeds tools, stops at barriers and respects budgets',{timeout:60000},async()=>{
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
 const result=await promisify(execFile)(python,['-B','tests/native_internal_test.py'],{windowsHide:true,timeout:45000,maxBuffer:1024*1024});
 assert.match(result.stderr,/Ran 13 tests/);assert.match(result.stderr,/\bOK\b/);
});
test('native output preserves service identity, actual receipts, unknown and hot-switch fences',{timeout:120000},async()=>{
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
 // The full SDK fixture takes about fifty seconds on Windows; 45s was flaky.
 const result=await promisify(execFile)(python,['-X','utf8','-B','tests/autonomous_pipeline_test.py'],{windowsHide:true,timeout:100000,maxBuffer:4*1024*1024});
 assert.match(result.stdout,/PIPELINE actual receipts/);assert.match(result.stdout,/missing message ID/);assert.match(result.stdout,/hot switch/);assert.match(result.stdout,/foreground interruption/);assert.match(result.stdout,/member unload preserves a late confirmed receipt/);
});
