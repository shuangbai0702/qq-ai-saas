import test from 'node:test';import assert from 'node:assert/strict';import path from 'node:path';import {execFile} from 'node:child_process';
test('memory synthesis: scoped self reports, topic recall, correction, limits and legacy persistence',async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['tests/hosted_memory_test.py'],{windowsHide:true},(e,stdout,stderr)=>e?reject(new Error(stdout+stderr)):resolve(stdout+stderr)));
 assert.match(out,/Ran 19 tests/);assert.match(out,/OK/);
});
