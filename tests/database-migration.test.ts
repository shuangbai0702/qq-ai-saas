import test from 'node:test';import assert from 'node:assert/strict';import{mkdtemp,mkdir,writeFile,readFile,rm}from'node:fs/promises';import path from'node:path';import os from'node:os';import{spawnSync}from'node:child_process';import{createApp}from'../apps/control-api/src/app.js';import{loadKey}from'../packages/shared/security.js';
function run(args:string[],env:NodeJS.ProcessEnv){return spawnSync(process.execPath,['--import','tsx','scripts/migrate-pglite-postgres.ts',...args],{cwd:process.cwd(),env:{...process.env,...env},encoding:'utf8',timeout:30000})}
test('PGlite export preserves the existing hex key, exports populated tenants and rejects corrupted snapshots',async()=>{
 const data=await mkdtemp(path.join(os.tmpdir(),'fish-migration-data-')),out=await mkdtemp(path.join(os.tmpdir(),'fish-migration-out-'));const file=path.join(out,'snapshot.json');let current:any;
 try{
  current=await createApp({root:data,agent:false});
  const key=await loadKey(data);await current.db.query("INSERT INTO users(id,email,password_hash,name) VALUES('migration-u1','one@example.test','hash','One'),('migration-u2','two@example.test','hash','Two')");
  await current.db.query("INSERT INTO tenants(id,name) VALUES('migration-t1','Tenant one'),('migration-t2','Tenant two')");
  await current.db.query("INSERT INTO memberships(user_id,tenant_id) VALUES('migration-u1','migration-t1'),('migration-u2','migration-t2')");
  await current.db.query("INSERT INTO audit_events(id,tenant_id,event,detail) VALUES(17,'migration-t1','probe','{\"n\":1}')");
  await current.app.close();current=undefined;
  const exp=run(['export',file],{DATA_ROOT:data,DATABASE_URL:''});assert.equal(exp.status,0,exp.stderr);
  const snap=JSON.parse(await readFile(file,'utf8'));assert.equal(snap.format,1);assert.equal(snap.tables.users.rows.length,2);assert.equal(snap.tables.tenants.rows.length,2);assert.match(snap.contentSha256,/^[a-f0-9]{64}$/);assert.equal(await readFile(path.join(data,'master.key'),'utf8'),key.toString('hex'));
  const corrupted=path.join(out,'corrupt.json');snap.tables.users.rows[0].name='tampered';await writeFile(corrupted,JSON.stringify(snap));
  const bad=run(['import',corrupted],{DATA_ROOT:data,DATABASE_URL:'postgresql://invalid/unused'});assert.notEqual(bad.status,0);assert.match(bad.stderr,/checksum/i);
  const lease=await import('../packages/database/lease.js').then(m=>m.acquireLease(data,'agent'));const blocked=run(['export',path.join(out,'blocked.json')],{DATA_ROOT:data,DATABASE_URL:''});await lease();assert.notEqual(blocked.status,0);assert.match(blocked.stderr,/already being held|lock/i);
  const noUrl=run(['import',file],{DATA_ROOT:data,DATABASE_URL:''});assert.notEqual(noUrl.status,0);assert.match(noUrl.stderr,/requires PostgreSQL settings/);
 }finally{await current?.app.close();await rm(data,{recursive:true,force:true});await rm(out,{recursive:true,force:true})}
});
test('content checksum normalizes audit bigint IDs across PGlite and PostgreSQL',async()=>{
 const {contentSha256,transferTables}=await import('../packages/database/transfer.js');
 const data=Object.fromEntries(transferTables.map(t=>[t,{rows:[]}])) as any;
 data.audit_events.rows=[{id:17,detail:{nested:['value']},created_at:new Date('2026-01-01T00:00:00Z')}];
 const pg=JSON.parse(JSON.stringify(data));pg.audit_events.rows[0].id='17';
 assert.equal(contentSha256(data),contentSha256(pg));
 pg.audit_events.rows[0].detail.nested[0]='changed';assert.notEqual(contentSha256(data),contentSha256(pg));
});
test('content checksum ignores tied SQL row order while preserving revisions and duplicate rows',async()=>{
 const {contentSha256,transferTables}=await import('../packages/database/transfer.js');
 const data=Object.fromEntries(transferTables.map(t=>[t,{rows:[]}])) as any;
 data.bot_config_revisions.rows=[{bot_id:'same',revision:1,config:{a:1}},{bot_id:'same',revision:2,config:{a:2}}];
 const expected=contentSha256(data);data.bot_config_revisions.rows.reverse();assert.equal(contentSha256(data),expected);
 data.bot_config_revisions.rows[0].config.a=3;assert.notEqual(contentSha256(data),expected);
 data.bot_config_revisions.rows[0].config.a=2;data.bot_config_revisions.rows.push({...data.bot_config_revisions.rows[0]});assert.notEqual(contentSha256(data),expected);
});
