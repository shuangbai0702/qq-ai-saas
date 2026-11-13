/** Private backup helper. Only counts and hashes may leave the protected root. */
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createHash,randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';
const cfg=JSON.parse((await fs.readFile('C:/FishBotPredeploy20261112/mobile-private-config.json','utf8')).replace(/^\uFEFF/,''));
const {Pool}=createRequire(cfg.sourceRoot+'/package.json')('pg');
const out=path.resolve(process.argv[2]);
if(!out.startsWith(path.resolve(cfg.stateRoot,'backups')+path.sep))throw Error('Backup output escaped protected state backups');
const e=cfg.environment;
if(e.PGHOST!=='127.0.0.1'||!/^\d+$/.test(String(e.PGPORT)))throw Error('Only configured loopback database is supported');
const password=(await fs.readFile(e.PGPASSWORD_FILE,'utf8')).replace(/\r?\n$/,'');
const options={host:e.PGHOST,port:Number(e.PGPORT),user:e.PGUSER,password,database:e.PGDATABASE,max:2,connectionTimeoutMillis:5000,statement_timeout:120000};
const env=Object.fromEntries(Object.entries(process.env).filter(([k])=>!k.toUpperCase().startsWith('PG')));
Object.assign(env,{PGHOST:e.PGHOST,PGPORT:String(e.PGPORT),PGUSER:e.PGUSER,PGPASSWORD:password,PGCONNECT_TIMEOUT:'5'});
const pool=new Pool(options),temporary='fishbot_b_release_restore_'+randomUUID().replaceAll('-','');
const quote=s=>'"'+String(s).replaceAll('"','""')+'"';
let created=false,restored,source,sourceTransaction=false;
async function run(name,args){
 const file=path.join(cfg.postgresBin,name+'.exe');await fs.access(file);
 await new Promise((resolve,reject)=>{
  const child=spawn(file,args,{env,windowsHide:true,stdio:['ignore','ignore','pipe']});let stderrBytes=0;
  child.stderr.on('data',b=>{stderrBytes+=b.length});
  const timer=setTimeout(()=>{child.kill();reject(Error('PostgreSQL backup utility timeout'))},300000);
  child.on('error',()=>{clearTimeout(timer);reject(Error('PostgreSQL backup utility unavailable'))});
  child.on('close',code=>{clearTimeout(timer);code===0?resolve():reject(Error('PostgreSQL '+name+' failed; private diagnostics withheld ('+stderrBytes+' bytes)'))});
 });
}
async function counts(client){
 const {rows}=await client.query("SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname");
 const value={};for(const {relname}of rows)value[relname]=String((await client.query('SELECT count(*)::text AS n FROM public.'+quote(relname))).rows[0].n);return value;
}
try{
 source=await pool.connect();await source.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');sourceTransaction=true;
 const snapshot=(await source.query('SELECT pg_export_snapshot() AS snapshot')).rows[0].snapshot;
 const expected=await counts(source),dump=path.join(out,'postgres.dump');
 if(await fs.stat(dump).then(()=>true,err=>{if(err.code==='ENOENT')return false;throw err}))throw Error('Existing backup archive');
 await run('pg_dump',['--dbname',e.PGDATABASE,'--format=custom','--no-owner','--no-privileges','--snapshot',snapshot,'--file',dump]);
 await source.query('ROLLBACK');sourceTransaction=false;source.release();source=undefined;
 await run('pg_restore',['--list',dump]);
 // Full-database dump restore must use a new isolated database: remapping its
 // public schema into the live database would put production rows at risk.
 await pool.query('CREATE DATABASE '+quote(temporary)+' TEMPLATE template0');created=true;
 await pool.query('REVOKE ALL ON DATABASE '+quote(temporary)+' FROM PUBLIC');
 await run('pg_restore',['--exit-on-error','--no-owner','--no-privileges','--dbname',temporary,dump]);
 restored=new Pool({...options,database:temporary,max:1});const actual=await counts(restored);
 if(JSON.stringify(actual)!==JSON.stringify(expected))throw Error('Isolated restore row counts differ from exported snapshot');
 const hash=createHash('sha256');for await(const chunk of createReadStream(dump))hash.update(chunk);const size=(await fs.stat(dump)).size,manifest={kind:'b-release-postgres-backup',snapshotConsistent:true,sha256:hash.digest('hex'),bytes:size,tableCounts:actual,restoreVerified:true,restoreIsolation:'invocation-owned temporary database',temporaryRemoved:false};
 await restored.end();restored=undefined;
 const {rows}=await pool.query('SELECT datname,pg_catalog.pg_get_userbyid(datdba)=current_user AS owned FROM pg_catalog.pg_database WHERE datname=$1',[temporary]);
 if(rows.length!==1||!rows[0].owned||rows[0].datname!==temporary)throw Error('Temporary restore database ownership changed');
 await pool.query('DROP DATABASE '+quote(temporary));created=false;
 if((await pool.query('SELECT 1 FROM pg_catalog.pg_database WHERE datname=$1',[temporary])).rows.length)throw Error('Temporary restore database remains');
 manifest.temporaryRemoved=true;await fs.writeFile(path.join(out,'postgres-manifest.json'),JSON.stringify(manifest));
 console.log(JSON.stringify({ok:true,postgresBackup:true,restoreVerified:true,tables:Object.keys(actual).length,bytes:manifest.bytes,sha256:manifest.sha256,temporaryRemoved:true}));
}catch(error){console.error(JSON.stringify({ok:false,phase:'postgres-backup',errorType:error?.name||'Error'}));process.exitCode=1}
finally{
 if(source){if(sourceTransaction)await source.query('ROLLBACK').catch(()=>{});source.release()}
 await restored?.end();
 if(created){
  const {rows}=await pool.query('SELECT pg_catalog.pg_get_userbyid(datdba)=current_user AS owned FROM pg_catalog.pg_database WHERE datname=$1',[temporary]);
  if(rows.length!==1||!rows[0].owned)throw Error('Refusing to drop unowned restore database');
  await pool.query('DROP DATABASE '+quote(temporary));
 }
 await pool.end();
}
