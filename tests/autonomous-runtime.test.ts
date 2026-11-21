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
 assert.match(result.stderr,/Ran 19 tests/);assert.match(result.stderr,/\bOK\b/);
});
test('unified main activation preserves shared Journal, identity, tools, restart and retired-entry rejection',{timeout:120000},async()=>{
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
 // Exercise the actual main executor; the separate message/research executor
 // was retired. Receipt/unknown/hot-switch behavior also has dedicated native
 // send_lifecycle and shared-lifecycle fixtures rather than a second executor.
 const result=await promisify(execFile)(python,['-X','utf8','-B','-m','unittest','activity_main_journal_test.Activity'],{env:{...process.env,PYTHONPATH:path.resolve('tests')},windowsHide:true,timeout:100000,maxBuffer:4*1024*1024});
 assert.match(result.stderr,/Ran 13 tests/);assert.match(result.stderr,/\bOK\b/);
});
