import 'dotenv/config';
import {createHash} from 'node:crypto';
import {chmod,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {PGlite} from '@electric-sql/pglite';
import {configuredDatabaseUrl,openDatabase} from '../packages/database/index.js';
import {acquireLease} from '../packages/database/lease.js';
import {contentSha256,expectedSchemaVersions,transferSchema as schema,transferTables as tables,validateTransfer,type TransferTable} from '../packages/database/transfer.js';

const root=path.resolve(process.env.DATA_ROOT||'.local');
const keyFile=path.join(root,'master.key');
const rawKey=process.env.MASTER_KEY??(await readFile(keyFile,'utf8')).trim();
if(!/^[a-f0-9]{64}$/i.test(rawKey))throw new Error('master.key must contain the existing 32-byte hex key');
const keySha256=createHash('sha256').update(Buffer.from(rawKey,'hex')).digest('hex');
const [mode,file]=process.argv.slice(2);
if(!['export','import'].includes(mode)||!file)throw new Error('Usage: migrate-pglite-postgres.ts export|import <protected-file>');
const absolute=path.resolve(file);
if(absolute===root||absolute.startsWith(root+path.sep))throw new Error('Snapshot must be outside the live data directory');
const seal=(body:any)=>createHash('sha256').update(JSON.stringify(body)).digest('hex');

if(mode==='export'){
 if(await configuredDatabaseUrl())throw new Error('Export source must be PGlite; unset PostgreSQL connection settings');
 const releaseAgent=await acquireLease(root,'agent');
 let pglite:PGlite|undefined;
 try{
  pglite=new PGlite(path.join(root,'postgres'));
  const versions=(await pglite.query<{version:string}>('SELECT version FROM schema_migrations ORDER BY version')).rows.map(x=>x.version);
  if(JSON.stringify(versions)!==JSON.stringify(expectedSchemaVersions))throw new Error('Unsupported source schema versions');
  const data={} as Record<TransferTable,{rows:any[]}>;
  for(const table of tables){
   const cols=(await pglite.query<{column_name:string}>('SELECT column_name FROM information_schema.columns WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position',['public',table])).rows;
   if(JSON.stringify(cols.map(x=>x.column_name))!==JSON.stringify(schema[table]))throw new Error(`Unexpected source columns in ${table}`);
   data[table]={rows:(await pglite.query(`SELECT * FROM ${table} ORDER BY 1`)).rows};
  }
  const sequence=(await pglite.query<{last_value:string;is_called:boolean}>('SELECT last_value::text,is_called FROM audit_events_id_seq')).rows[0];
  if(!sequence)throw new Error('Could not read audit_events sequence state');
  const body={format:1 as const,createdAt:new Date().toISOString(),schemaVersions:versions,keySha256,sequence,tables:data,contentSha256:contentSha256(data)};
  const snapshot={...body,sha256:seal(body)};
  await writeFile(absolute,JSON.stringify(snapshot),{flag:'wx',mode:0o600});await chmod(absolute,0o600);
  console.log(JSON.stringify({result:'exported',sha256:snapshot.sha256,contentSha256:snapshot.contentSha256,sequence:snapshot.sequence,tables:Object.fromEntries(tables.map(t=>[t,snapshot.tables[t].rows.length]))}));
 }finally{try{await pglite?.close()}finally{await releaseAgent()}}
}else{
 const databaseUrl=await configuredDatabaseUrl();if(!databaseUrl)throw new Error('Import target requires PostgreSQL settings');
 const snapshot=JSON.parse(await readFile(absolute,'utf8'));
 const{sha256,...body}=snapshot;
 if(snapshot.format!==1||seal(body)!==sha256)throw new Error('Snapshot checksum mismatch');
 if(!snapshot.sequence||!/^\d+$/.test(String(snapshot.sequence.last_value))||typeof snapshot.sequence.is_called!=='boolean')throw new Error('Malformed audit_events sequence state');
 if(snapshot.keySha256!==keySha256)throw new Error('Target master.key does not match source');
 if(JSON.stringify(snapshot.schemaVersions)!==JSON.stringify(expectedSchemaVersions))throw new Error('Unsupported snapshot schema versions');
 validateTransfer(snapshot.tables);
 if(snapshot.contentSha256!==contentSha256(snapshot.tables))throw new Error('Snapshot content checksum mismatch');
  const db=await openDatabase(root,databaseUrl);
  try{
  const migrated=await db.transaction(async tx=>{
   await tx.query('LOCK TABLE '+tables.map(t=>`"${t}"`).join(',')+' IN ACCESS EXCLUSIVE MODE');
   const versions=(await tx.query<{version:string}>('SELECT version FROM schema_migrations ORDER BY version')).map(x=>x.version);
   if(JSON.stringify(versions)!==JSON.stringify(expectedSchemaVersions))throw new Error('PostgreSQL target has an unexpected schema; refusing overwrite');
   for(const table of tables){const[row]=await tx.query<{count:string}>(`SELECT count(*)::text AS count FROM "${table}"`);if(Number(row.count)!==0)throw new Error('PostgreSQL target contains data; refusing overwrite')}
   const counts={} as Record<TransferTable,number>;
   for(const table of tables){
    const rows=snapshot.tables[table].rows;
    for(const row of rows){
     const columns=schema[table];
     const values=columns.map(c=>row[c]);
     await tx.query(`INSERT INTO "${table}" (${columns.map(c=>`"${c}"`).join(',')}) VALUES (${values.map((_,i)=>`$${i+1}`).join(',')})`,values);
    }
    counts[table]=rows.length;
   }
   // RESTART is transactional; setval is not rolled back on transaction failure.
   const maxId=snapshot.tables.audit_events.rows.reduce((max:bigint,row:any)=>BigInt(row.id)>max?BigInt(row.id):max,0n);
   const sourceNext=BigInt(snapshot.sequence.last_value)+(snapshot.sequence.is_called?1n:0n);
   const nextId=sourceNext>maxId?sourceNext:maxId+1n;
   await tx.query(`ALTER SEQUENCE audit_events_id_seq RESTART WITH ${nextId}`);
   const restored={} as Record<TransferTable,{rows:any[]}>;
   for(const table of tables)restored[table]={rows:await tx.query(`SELECT * FROM "${table}" ORDER BY 1`)};
   if(contentSha256(restored)!==snapshot.contentSha256)throw new Error('In-transaction imported content verification failed');
   const sequence=(await tx.query<{last_value:string;is_called:boolean}>('SELECT last_value::text,is_called FROM audit_events_id_seq'))[0];
   if(!sequence||sequence.last_value!==String(nextId)||sequence.is_called)throw new Error('In-transaction sequence verification failed');
   return counts;
  });
  console.log(JSON.stringify({result:'imported',snapshotSha256:sha256,contentSha256:snapshot.contentSha256,sequence:snapshot.sequence,tables:migrated}));
 }finally{await db.close()}
}
