import {acquireLease} from './lease.js';
import { PGlite } from '@electric-sql/pglite';
import pg from 'pg';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { seedPGliteIfMissing } from './template.js';
export interface Sql {query<T=any>(sql:string,params?:any[]):Promise<T[]>;transaction<T>(fn:(tx:Sql)=>Promise<T>):Promise<T>;close():Promise<void>}
export async function openDatabase(dataRoot:string,url?:string):Promise<Sql>{
  let db:Sql|undefined;let release:(()=>Promise<void>)|undefined;
  if(!url){await mkdir(dataRoot,{recursive:true});release=await acquireLease(path.resolve(dataRoot),'database')}
  try{
  if(url){
    const pool=new pg.Pool({connectionString:url,max:8});
    const wrap=(client:pg.Pool|pg.PoolClient):Sql=>({query:async(sql,params)=>(await client.query(sql,params)).rows,transaction:async(fn)=>{const c=await pool.connect();try{await c.query('BEGIN');const out=await fn(wrap(c));await c.query('COMMIT');return out}catch(e){await c.query('ROLLBACK');throw e}finally{c.release()}},close:()=>pool.end()});
    db=wrap(pool);
  }else{
    await mkdir(dataRoot,{recursive:true});
    await seedPGliteIfMissing(dataRoot);
    // Single-process SaaS metadata workload: retain PostgreSQL semantics and
    // durability while avoiding 128 MiB of default shared buffers per database.
    // balanced keeps upstream defaults for larger workloads or regression tests.
    const profile=process.env.DB_MEMORY_PROFILE||'compact';
    if(!['compact','balanced'].includes(profile))throw new Error('DB_MEMORY_PROFILE must be compact or balanced');
    const options=profile==='balanced'?{}:{startParams:[...PGlite.defaultStartParams,'-c','shared_buffers=8MB','-c','work_mem=1MB','-c','maintenance_work_mem=8MB']};
    const pglite=new PGlite(path.join(dataRoot,'postgres'),options);
    let tail:Promise<unknown>=Promise.resolve();
    const serial=<T>(fn:()=>Promise<T>)=>{const p=tail.then(fn);tail=p.catch(()=>{});return p};
    const inner:Sql={query:async(sql,params)=>(await pglite.query<any>(sql,params)).rows,transaction:async(fn)=>pglite.transaction(async tx=>fn({...inner,query:async(sql,params)=>(await tx.query<any>(sql,params)).rows})),close:()=>pglite.close()};
    db={query:(sql,params)=>serial(()=>inner.query(sql,params)),transaction:fn=>serial(()=>inner.transaction(fn)),close:()=>serial(()=>inner.close())};
  }
  await db.query('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY,applied_at TIMESTAMPTZ DEFAULT now())');
  for(const [version,file] of [['001','001_initial.sql'],['002','002_runtime_groups.sql'],['003','003_invitations.sql'],['004','004_bot_quotas.sql'],['005','005_qq_binding_kinds.sql']])if(!(await db.query('SELECT version FROM schema_migrations WHERE version=$1',[version])).length){
    const sql=await readFile(fileURLToPath(new URL('./migrations/'+file,import.meta.url)),'utf8');
    await db.transaction(async tx=>{for(const statement of sql.split(';').filter(x=>x.trim()))await tx.query(statement);await tx.query('INSERT INTO schema_migrations(version) VALUES($1)',[version])});
  }
  const connection=db;let closed:Promise<void>|undefined;db={...connection,close:()=>closed??=(async()=>{try{await connection.close()}finally{await release?.()}})()};
  return db;
  }catch(error){await db?.close().catch(()=>{});await release?.();throw error}
}
