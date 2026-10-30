import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,readFile,mkdir} from 'node:fs/promises';import path from 'node:path';import {ProcessHost} from '../apps/node-agent/src/process-host.js';
test('process logs redact pipe-split credentials and reject oversized lines',{timeout:15000},async()=>{
 const root=await mkdtemp(path.resolve('.local/log-safety-'));const botRoot=path.join(root,'bots','log-test');await mkdir(botRoot,{recursive:true});const script=path.join(botRoot,'fixture.cjs');
 await writeFile(script,"process.stdout.write('synthetic-');setTimeout(()=>{process.stdout.write('private-key\\n');process.stdout.write('x'.repeat(70000));process.stdout.write('synthetic-private-key\\n');process.stdout.write('finished\\n')},500)");
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));const host=new ProcessHost(root,python);await host.start('log-test','probe',process.execPath,[script],botRoot,process.env,['synthetic-private-key']);
 const child=host.children.get('log-test:probe');if(child)await new Promise(r=>child.once('close',r));await host.logWrites.get('log-test');const log=await readFile(path.join(botRoot,'runtime.log'),'utf8');assert.ok(log.includes('[REDACTED]'));assert.ok(!log.includes('synthetic-private-key'));assert.ok(log.includes('finished'));assert.ok(log.length<1000);assert.equal(host.children.has('log-test:probe'),false);
});

test('one bounded inspection sweep validates PID ownership and invalidates before stop',{timeout:15000},async()=>{
 const root=await mkdtemp(path.resolve('.local/inspection-')),id='probe';const cwd=path.join(root,'bots',id);await mkdir(cwd,{recursive:true});const script=path.join(cwd,'fixture.cjs');await writeFile(script,'setInterval(()=>{},1000)');
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));const host=new ProcessHost(root,python);
 try{
  await host.start(id,'ai',process.execPath,[script],cwd,process.env);const first=await host.inspect(id,'ai');assert.ok(first?.pid);
  await host.beginInspection();assert.equal((await host.inspect(id,'ai')).pid,first.pid);assert.equal(await host.inspect('unknown','ai'),null);
  await host.stop(id,'ai');assert.equal(await host.inspect(id,'ai'),null,'snapshot invalidated on stop');
  await writeFile(host.file(id,'ai'),JSON.stringify({pid:process.pid,created:0}));await host.beginInspection();assert.equal(await host.inspect(id,'ai'),null,'wrong argv/creation never accepted');host.endInspection();
  assert.equal(host.secrets.size,0,'rotated keys are not retained by host');
 }finally{host.endInspection();await import('node:fs/promises').then(fs=>fs.rm(host.file(id,'ai'),{force:true}))}
});

test('immediate process exit is observed before asynchronous PID inspection',{timeout:15000},async()=>{
 const root=await mkdtemp(path.resolve('.local/instant-exit-')),cwd=path.join(root,'bots','short');await mkdir(cwd,{recursive:true});const script=path.join(cwd,'fixture.cjs');await writeFile(script,"console.log('synthetic-current-key');process.exit(7)");
 const host=new ProcessHost(root,path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python')));
 await host.start('short','ai',process.execPath,[script],cwd,process.env,['synthetic-current-key']);
 const child=host.children.get('short:ai');if(child)await new Promise(r=>child.once('close',r));await host.logWrites.get('short');
 assert.equal(host.children.size,0);const log=await readFile(path.join(cwd,'runtime.log'),'utf8');assert.match(log,/process exited \(7\)/);assert.ok(!log.includes('synthetic-current-key'));assert.match(log,/REDACTED/);
});
