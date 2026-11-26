import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,readFile,mkdir} from 'node:fs/promises';import path from 'node:path';import {ProcessHost} from '../apps/node-agent/src/process-host.js';
test('process logs redact pipe-split credentials and reject oversized lines',{timeout:15000},async()=>{
 const root=await mkdtemp(path.resolve('.local/log-safety-'));const botRoot=path.join(root,'bots','log-test');await mkdir(botRoot,{recursive:true});const script=path.join(botRoot,'fixture.cjs');
 const prior='previous evidence\n'+'x'.repeat(2*1024*1024);await writeFile(path.join(botRoot,'runtime.log'),prior);
 await writeFile(script,"process.stdout.write('synthetic-');setTimeout(()=>{process.stdout.write('private-key\\n');process.stdout.write('x'.repeat(70000));process.stdout.write('synthetic-private-key\\n');process.stdout.write('finished\\n')},500)");
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));const host=new ProcessHost(root,python);await host.start('log-test','probe',process.execPath,[script],botRoot,process.env,['synthetic-private-key']);
 const child=host.children.get('log-test:probe');if(child)await new Promise(r=>child.once('close',r));await host.logWrites.get('log-test');const log=await readFile(path.join(botRoot,'runtime.log'),'utf8');assert.ok(log.includes('[REDACTED]'));assert.ok(!log.includes('synthetic-private-key'));assert.ok(log.includes('finished'));assert.ok(log.length<1000);assert.equal(host.children.has('log-test:probe'),false);assert.equal(await readFile(path.join(botRoot,'runtime.log.previous'),'utf8'),prior,'rotation preserves the previous evidence');
});

test('one bounded inspection sweep validates PID ownership and invalidates before stop',{timeout:15000},async()=>{
 const root=await mkdtemp(path.resolve('.local/inspection-')),id='probe';const cwd=path.join(root,'bots',id);await mkdir(cwd,{recursive:true});const script=path.join(cwd,'fixture.cjs');await writeFile(script,'setInterval(()=>{},1000)');
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));const host=new ProcessHost(root,python);
 try{
  await host.start(id,'ai',process.execPath,[script],cwd,process.env);const first=await host.inspect(id,'ai');assert.ok(first?.pid);
  await host.beginInspection();assert.equal((await host.inspect(id,'ai')).pid,first.pid);assert.equal(await host.inspect('unknown','ai'),null);
  const child=host.children.get(`${id}:ai`)!;const closed=new Promise<void>(resolve=>child.once('close',()=>resolve()));
  await host.stop(id,'ai');await closed;await host.logWrites.get(id);assert.equal(await host.inspect(id,'ai'),null,'snapshot invalidated on stop');
  const stopped=await readFile(path.join(cwd,'runtime.log'),'utf8');assert.match(stopped,/stop requested .*intent=managed-stop/);assert.match(stopped,/process exited .* signal=.* intent=managed-stop/);
  await writeFile(host.file(id,'ai'),JSON.stringify({pid:process.pid,created:0}));await host.beginInspection();assert.equal(await host.inspect(id,'ai'),null,'wrong argv/creation never accepted');host.endInspection();
 }finally{host.endInspection();await import('node:fs/promises').then(fs=>fs.rm(host.file(id,'ai'),{force:true}))}
});

test('externally signaled child records signal separately from exit code',{timeout:15000},async()=>{
 const root=await mkdtemp(path.resolve('.local/signal-exit-')),cwd=path.join(root,'bots','signal');await mkdir(cwd,{recursive:true});const script=path.join(cwd,'fixture.cjs');await writeFile(script,'setInterval(()=>{},1000)');
 const host=new ProcessHost(root,path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python')));
 await host.start('signal','ai',process.execPath,[script],cwd,process.env);const child=host.children.get('signal:ai')!;const closed=new Promise<void>(resolve=>child.once('close',()=>resolve()));
 child.kill('SIGTERM');await closed;await host.logWrites.get('signal');
 assert.match(await readFile(path.join(cwd,'runtime.log'),'utf8'),/process exited \(null\) at=.* signal=SIGTERM intent=unexpected-or-external/);
});

test('immediate process exit is observed before asynchronous PID inspection',{timeout:15000},async()=>{
 const root=await mkdtemp(path.resolve('.local/instant-exit-')),cwd=path.join(root,'bots','short');await mkdir(cwd,{recursive:true});const script=path.join(cwd,'fixture.cjs');await writeFile(script,"console.log('synthetic-current-key');process.exit(7)");
 const host=new ProcessHost(root,path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python')));
 await host.start('short','ai',process.execPath,[script],cwd,process.env,['synthetic-current-key']);
 const child=host.children.get('short:ai');if(child)await new Promise(r=>child.once('close',r));await host.logWrites.get('short');
 assert.equal(host.children.size,0);const log=await readFile(path.join(cwd,'runtime.log'),'utf8');assert.match(log,/process exited \(7\)/);assert.ok(!log.includes('synthetic-current-key'));assert.match(log,/REDACTED/);
 assert.match(log,/process exited \(7\) at=.* pid=\d+ signal=none intent=unexpected-or-external/);
});

test('unreadable or malformed process records are not absence and cannot launch a duplicate',async()=>{
 const root=await mkdtemp(path.resolve('.local/record-ownership-')),cwd=path.join(root,'bots','owned');await mkdir(cwd,{recursive:true});
 const host=new ProcessHost(root,path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'))),file=host.file('owned','ai');
 await mkdir(file);await assert.rejects(host.record('owned','ai'));
 await import('node:fs/promises').then(fs=>fs.rmdir(file));
 for(const value of ['{','null','[]','{"pid":"123"}']){
  await writeFile(file,value);await assert.rejects(host.record('owned','ai'));
  await assert.rejects(host.start('owned','ai',process.execPath,['--eval','process.exit(0)'],cwd,process.env));
  assert.equal(host.children.size,0);assert.equal(await readFile(file,'utf8'),value);
 }
});

test('a damaged record is isolated from peers and invalidated snapshots require fresh proof',async()=>{
 const root=await mkdtemp(path.resolve('.local/record-sweep-'));for(const id of ['damaged','healthy'])await mkdir(path.join(root,'bots',id),{recursive:true});
 const host:any=new ProcessHost(root,'unused');await writeFile(host.file('damaged','ai'),'{');await writeFile(host.file('healthy','ai'),JSON.stringify({pid:123,created:1}));
 const inspected:string[]=[];host.helper=async(args:string[],input?:string)=>{
  if(args[0]==='batch'){const records=JSON.parse(input!);assert.deepEqual(records.map((v:any)=>v.id),['healthy']);return JSON.stringify(records.map((v:any)=>({...v,valid:true})))}
  assert.equal(args[0],'inspect');inspected.push(args[1]);return JSON.stringify({pid:123,created:1,valid:true});
 };host.maintainMemory=async(records:any[])=>{assert.deepEqual(records.map(v=>v.id),['healthy'])};
 await host.withInspection(async()=>{
  await host.beginInspection();await assert.rejects(host.inspect('damaged','ai'),/进程记录无法核实/);
  assert.equal((await host.inspect('healthy','ai')).pid,123);assert.deepEqual(inspected,[]);
  await host.start('healthy','ai','unused',[],'.',{});assert.deepEqual(inspected,['123'],'a cleared cache entry must recheck the actual owner');assert.equal(host.children.size,0);
  await assert.rejects(host.start('damaged','ai','unused',[],'.',{}),/进程记录无法解析/);assert.equal(await readFile(host.file('damaged','ai'),'utf8'),'{');
 });
});
