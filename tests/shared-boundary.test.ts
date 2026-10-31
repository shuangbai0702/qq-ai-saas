import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {AstrBotAdapter} from '../adapters/astrbot/index.js';
import {defaultConfig,type RuntimeContext} from '../packages/contracts/index.js';

test('shared IPC and tool boundaries accept generated configuration and reject malformed input before side effects',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-shared-boundary-'));
 try{
  const ctx:RuntimeContext={botId:'bot-A',tenantId:'tenant-A',root,generation:1,revision:2,aiPort:24000,protocolPort:24002,token:'a'.repeat(64),config:defaultConfig,apiKey:'fixture-key',expectedUin:''};
  await new AstrBotAdapter({inspect:async()=>undefined} as any,'','').provision(ctx);
  const config=JSON.parse(await readFile(path.join(root,'ai/data/cmd_config.json'),'utf8'));
  const body={generation:ctx.generation,revision:ctx.revision,prompt:ctx.config.systemPrompt,key:ctx.apiKey,config,groups:ctx.config.groups,uin:ctx.expectedUin,platform:config.platform[0],runtimeRoot:root,behaviorConfig:ctx.config,botId:ctx.botId};
  const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
  const out=await new Promise<string>((resolve,reject)=>{
   const child=execFile(python,['-B','tests/shared_boundary_test.py'],{windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}},(error,stdout,stderr)=>error?reject(new Error(stdout+'\n'+stderr)):resolve(stdout));
   child.stdin!.end(JSON.stringify(body));
  });
  assert.match(out,/PASS 25 malformed PUT cases/);
 }finally{await rm(root,{recursive:true,force:true})}
});
