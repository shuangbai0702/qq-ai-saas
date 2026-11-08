import test from 'node:test';import assert from 'node:assert/strict';import {EventEmitter} from 'node:events';import pg from 'pg';
import {openDatabase,postgresPoolOptions} from '../packages/database/index.js';

test('PostgreSQL pool uses bounded server statement and lock cancellation',()=>{
 const names=['PG_POOL_MAX','PG_STATEMENT_TIMEOUT_MS','PG_LOCK_TIMEOUT_MS','PG_IDLE_TRANSACTION_TIMEOUT_MS'];const previous=Object.fromEntries(names.map(name=>[name,process.env[name]]));
 try{
  for(const name of names)delete process.env[name];const options=postgresPoolOptions('postgresql://fixture');
  assert.equal(options.max,4);assert.equal(options.statement_timeout,30000);assert.equal(options.lock_timeout,10000);assert.equal(options.idle_in_transaction_session_timeout,180000);assert.equal(options.connectionTimeoutMillis,5000);assert.equal('query_timeout' in options,false,'client timeout alone cannot cancel a server statement');
  process.env.PG_POOL_MAX='3';process.env.PG_STATEMENT_TIMEOUT_MS='45000';assert.equal(postgresPoolOptions('fixture').max,3);assert.equal(postgresPoolOptions('fixture').statement_timeout,45000);
  for(const value of ['0','-1','oops','1.5']){process.env.PG_STATEMENT_TIMEOUT_MS=value;assert.throws(()=>postgresPoolOptions('fixture'),/positive integer/)}
 }finally{for(const name of names){if(previous[name]===undefined)delete process.env[name];else process.env[name]=previous[name]}}
});

test('statement failure rolls back; broken rollback discards connection and preserves original error',async()=>{
 const OriginalPool=pg.Pool,prior=process.env.DB_AUTO_MIGRATE;process.env.DB_AUTO_MIGRATE='false';let pool:any;
 class FixturePool extends EventEmitter{
  operations:string[]=[];released:boolean[]=[];rollbackFailure=false;ended=false;
  constructor(public options:any){super();pool=this}
  async query(){return{rows:Array.from({length:11},(_,i)=>({version:String(i+1).padStart(3,'0')}))}}
  async connect(){return{query:async(sql:string)=>{this.operations.push(sql);if(sql==='ROLLBACK'&&this.rollbackFailure)throw new Error('broken socket');return{rows:[]}},release:(discard:boolean)=>this.released.push(discard)}}
  async end(){this.ended=true}
 }
 (pg as any).Pool=FixturePool;
 try{
  const db=await openDatabase('unused','postgresql://fixture');const failure=Object.assign(new Error('statement cancelled'),{code:'57014'});
  await assert.rejects(db.transaction(async()=>{throw failure}),error=>error===failure);assert.deepEqual(pool.operations,['BEGIN','ROLLBACK']);assert.deepEqual(pool.released,[false]);
  pool.rollbackFailure=true;await assert.rejects(db.transaction(async()=>{throw failure}),error=>error===failure);assert.deepEqual(pool.released,[false,true]);
  await db.close();assert.equal(pool.ended,true);
 }finally{(pg as any).Pool=OriginalPool;if(prior===undefined)delete process.env.DB_AUTO_MIGRATE;else process.env.DB_AUTO_MIGRATE=prior}
});
