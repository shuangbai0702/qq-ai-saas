// Finite operator rollout; never prints prompts, QQ sessions, model keys or DB URL.
import pg from 'pg';import {isDeepStrictEqual} from 'node:util';import {randomUUID} from 'node:crypto';import path from 'node:path';import {writeFile,readFile} from 'node:fs/promises';
import {configuredDatabaseUrl} from '../dist-server/packages/database/index.js';
import {disableNativeQQFaces} from '../dist-server/packages/shared/native-face-policy.js';
const action=process.argv[2]||'inspect';if(!['inspect','apply','rollback'].includes(action))throw new Error('Expected inspect, apply or rollback');
const backup=path.resolve(process.argv[3]||path.join(process.env.DATA_ROOT||'.local','native-face-policy-backup-'+Date.now()+'.json'));
const url=await configuredDatabaseUrl();if(!url)throw new Error('Explicit PostgreSQL configuration required');
const client=new pg.Client({connectionString:url,connectionTimeoutMillis:5000});await client.connect();
try{
 await client.query('BEGIN');await client.query("SET LOCAL lock_timeout='5s'");
 const result=await client.query("SELECT b.id,b.tenant_id,b.revision,b.generation,b.desired_state,b.runtime_mode,r.config FROM bots b JOIN bot_config_revisions r ON r.bot_id=b.id AND r.revision=b.revision WHERE b.deleted_at IS NULL AND b.desired_state!='deleting' ORDER BY b.id FOR UPDATE OF b");
 const saved=action==='rollback'?JSON.parse(await readFile(backup,'utf8')):null;const changes=[];
 for(const bot of result.rows){
  const prior=saved?.rows.find(row=>row.id===bot.id);let config;
  if(action==='rollback'){
   if(!prior)continue;
   if(bot.revision!==prior.nextRevision||bot.generation!==prior.generation||!isDeepStrictEqual(bot.config,disableNativeQQFaces(prior.config)))throw new Error('Rollback refused: lifecycle/configuration changed');
   config=prior.config;
  }else config=disableNativeQQFaces(bot.config);
  if(isDeepStrictEqual(config,bot.config))continue;
  if(bot.desired_state==='running'&&bot.runtime_mode!=='shared')throw new Error('Live dedicated AI requires separate maintenance; no restart performed');
  changes.push({bot,config});
 }
 if(action==='inspect'){await client.query('ROLLBACK');console.log(JSON.stringify({action,candidates:changes.length,running:changes.filter(c=>c.bot.desired_state==='running').length}));}
 else{
  if(action==='apply')await writeFile(backup,JSON.stringify({createdAt:new Date().toISOString(),rows:changes.map(({bot})=>({...bot,nextRevision:bot.revision+1}))}),{mode:0o600,flag:'wx'});
  for(const {bot,config} of changes){
   const revision=bot.revision+1;
   await client.query('INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES($1,$2,$3)',[bot.id,revision,JSON.stringify(config)]);
   await client.query('UPDATE bots SET revision=$2 WHERE id=$1',[bot.id,revision]);
   if(bot.desired_state==='running')await client.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'apply')",[randomUUID(),bot.id,'native-faces-'+revision,bot.generation]);
   await client.query('INSERT INTO audit_events(tenant_id,event,bot_id,detail) VALUES($1,$2,$3,$4)',[bot.tenant_id,'qq.native_faces.'+(action==='apply'?'disabled':'restored'),bot.id,JSON.stringify({revision,previousRevision:bot.revision})]);
  }
  await client.query('COMMIT');console.log(JSON.stringify({action,updated:changes.length,queued:changes.filter(c=>c.bot.desired_state==='running').length,backup}));
 }
}catch(error){await client.query('ROLLBACK');throw new Error('Native face policy '+action+' failed: '+(error.code||error.message));}
finally{await client.end()}
