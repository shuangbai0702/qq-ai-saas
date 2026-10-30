import test from'node:test';import assert from'node:assert/strict';import{execFile}from'node:child_process';
test('two tenants share a real AstrBot with isolated models, keys, persona, history, image routing and live updates',{timeout:180000},async()=>{
 const out=await new Promise<string>((resolve,reject)=>execFile(process.execPath,['--max-old-space-size=128','--import','tsx','experiments/round2-shared/run.ts','2','2'],{windowsHide:true,maxBuffer:1024*1024},(error,stdout,stderr)=>error?reject(new Error(stdout+'\n'+stderr)):resolve(stdout)));
 assert.match(out,/PASS .*run-2bots-2per-/);
});
