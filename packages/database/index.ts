import {acquireLease} from './lease.js';
import { PGlite } from '@electric-sql/pglite';
import pg from 'pg';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { seedPGliteIfMissing } from './template.js';
export interface Sql {query<T=any>(sql:string,params?:any[]):Promise<T[]>;transaction<T>(fn:(tx:Sql)=>Promise<T>):Promise<T>;close():Promise<void>}
export function postgresPoolOptions(url:string){
 const timeout=(name:string,fallback:number)=>{const value=process.env[name];if(value===undefined)return fallback;const parsed=Number(value);if(!Number.isSafeInteger(parsed)||parsed<=0)throw new Error(`${name} must be a positive integer`);return parsed};
 return {connectionString:url,max:Math.min(32,Math.max(1,Math.floor(Number(process.env.PG_POOL_MAX)||4))),idleTimeoutMillis:30000,connectionTimeoutMillis:5000,allowExitOnIdle:false,
  // Server-side cancellation preserves the query outcome/transaction contract;
  // a JS Promise timeout alone would leave the statement running in the pool.
  statement_timeout:timeout('PG_STATEMENT_TIMEOUT_MS',30000),lock_timeout:timeout('PG_LOCK_TIMEOUT_MS',10000),idle_in_transaction_session_timeout:timeout('PG_IDLE_TRANSACTION_TIMEOUT_MS',180000)};
}
export async function configuredDatabaseUrl(){
 if(process.env.DATABASE_URL)return process.env.DATABASE_URL;
 if(!process.env.PGHOST)return undefined;
 const password=process.env.PGPASSWORD_FILE?(await readFile(process.env.PGPASSWORD_FILE,'utf8')).replace(/\r?\n$/,''):process.env.PGPASSWORD||'';
 const url=new URL(`postgresql://${encodeURIComponent(process.env.PGUSER||'postgres')}@${process.env.PGHOST}:${process.env.PGPORT||'5432'}/${encodeURIComponent(process.env.PGDATABASE||'postgres')}`);
 url.password=password;if(process.env.PGSSLMODE==='require')url.searchParams.set('sslmode','require');return url.toString();
}

export async function openDatabase(dataRoot:string,url?:string):Promise<Sql>{
  let db:Sql|undefined;let release:(()=>Promise<void>)|undefined;
  if(!url){await mkdir(dataRoot,{recursive:true});release=await acquireLease(path.resolve(dataRoot),'database')}
  try{
  if(url){
    const pool=new pg.Pool(postgresPoolOptions(url));
    pool.on('error',error=>console.error('PostgreSQL idle connection error:',(error as NodeJS.ErrnoException).code||error.name));
    let savepoint=0;
    const wrap=(client:pg.PoolClient):Sql=>({
      query:async(sql,params)=>(await client.query(sql,params)).rows,
      transaction:async fn=>{
        const name=`nested_${++savepoint}`;
        await client.query(`SAVEPOINT ${name}`);
        try{const result=await fn(wrap(client));await client.query(`RELEASE SAVEPOINT ${name}`);return result}
        catch(error){await client.query(`ROLLBACK TO SAVEPOINT ${name}`);await client.query(`RELEASE SAVEPOINT ${name}`);throw error}
      },
      close:async()=>{throw new Error('Transaction does not own the database connection')}
    });
    db={query:async(sql,params)=>(await pool.query(sql,params)).rows,transaction:async fn=>{
      const client=await pool.connect();
      let discard=false;
      try{await client.query('BEGIN');const result=await fn(wrap(client));await client.query('COMMIT');return result}
      catch(error){try{await client.query('ROLLBACK')}catch{discard=true}throw error}finally{client.release(discard)}
    },close:()=>pool.end()};
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
  const versionsExpected=['001','002','003','004','005','006','007','008','009','010','011'];
  if(process.env.DB_AUTO_MIGRATE==='false'){
    let versions:string[];
    try{versions=(await db.query<{version:string}>('SELECT version FROM schema_migrations ORDER BY version')).map(x=>x.version)}catch{throw new Error('Database schema is missing; run the explicit migration command')}
    if(JSON.stringify(versions)!==JSON.stringify(versionsExpected))throw new Error('Database schema is not current; run the explicit migration command');
  }else await db.transaction(async tx=>{
    if(url)await tx.query('SELECT pg_advisory_xact_lock(18435, 1)');
    await tx.query('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY,applied_at TIMESTAMPTZ DEFAULT now())');
    for(const [version,file] of [['001','001_initial.sql'],['002','002_runtime_groups.sql'],['003','003_invitations.sql'],['004','004_bot_quotas.sql'],['005','005_qq_binding_kinds.sql'],['006','006_autonomous_lifecycle.sql'],['007','007_shared_autonomous_policy.sql'],['008','008_inner_owned_capabilities.sql'],['009','009_bilibili_tools.sql'],['010','010_bilibili_media_jobs.sql'],['011','011_outer_context_cycles.sql']])if(!(await tx.query('SELECT version FROM schema_migrations WHERE version=$1',[version])).length){
      const sql=await readFile(fileURLToPath(new URL('./migrations/'+file,import.meta.url)),'utf8');
      for(const statement of sql.split(';').filter(x=>x.trim()))await tx.query(statement);
      await tx.query('INSERT INTO schema_migrations(version) VALUES($1)',[version]);
    }
  });
  const connection=db;let closed:Promise<void>|undefined;db={...connection,close:()=>closed??=(async()=>{try{await connection.close()}finally{await release?.()}})()};
  return db;
  }catch(error){await db?.close().catch(()=>{});await release?.();throw error}
}
