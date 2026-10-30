import test from'node:test';import assert from'node:assert/strict';import{mkdtemp}from'node:fs/promises';import os from'node:os';import path from'node:path';import{openDatabase}from'../packages/database/index.js';
test('compact PGlite profile applies actual PostgreSQL limits without losing durable migrations',async()=>{
 const original=process.env.DB_MEMORY_PROFILE;delete process.env.DB_MEMORY_PROFILE;
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-compact-db-'));
 try{let db=await openDatabase(root);const settings=await db.query("SELECT name,setting,unit FROM pg_settings WHERE name IN ('shared_buffers','work_mem','maintenance_work_mem')");assert.equal(settings.find(s=>s.name==='shared_buffers').setting,'1024');assert.equal(settings.find(s=>s.name==='work_mem').setting,'1024');assert.equal(settings.find(s=>s.name==='maintenance_work_mem').setting,'8192');
  await db.query("INSERT INTO tenants(id,name) VALUES('compact-test','persistent')");await db.close();db=await openDatabase(root);try{assert.equal((await db.query("SELECT name FROM tenants WHERE id='compact-test'"))[0].name,'persistent');assert.ok((await db.query('SELECT * FROM schema_migrations')).length>0)}finally{await db.close()}
  process.env.DB_MEMORY_PROFILE='balanced';db=await openDatabase(await mkdtemp(path.join(os.tmpdir(),'fish-balanced-db-')));try{assert.equal((await db.query("SHOW shared_buffers"))[0].shared_buffers,'128MB')}finally{await db.close()}
 }finally{if(original===undefined)delete process.env.DB_MEMORY_PROFILE;else process.env.DB_MEMORY_PROFILE=original}
});
