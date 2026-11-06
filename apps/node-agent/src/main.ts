import 'dotenv/config';
import path from 'node:path';
import os from 'node:os';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile,rm} from 'node:fs/promises';
import {openDatabase, configuredDatabaseUrl} from '../../../packages/database/index.js';
import {loadKey} from '../../../packages/shared/security.js';
import {NodeAgent,HeartbeatWriter} from './agent.js';

const databaseUrl = await configuredDatabaseUrl();
if (!databaseUrl) throw new Error('独立Agent需要PostgreSQL；PGlite仅支持嵌入式单进程模式');
const root = path.resolve(process.env.DATA_ROOT || '.local');
// An optional deployment contract protects native QQ sessions from a container
// hostname change. The host supervisor restores the UTS identity before boot.
if(process.platform==='linux'){
  let expected:string|undefined;
  try{expected=JSON.parse(await readFile(path.join(root,'host-identity.json'),'utf8')).hostname}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error}
  if(expected){
    if(!/^[a-zA-Z0-9.-]{1,64}$/.test(expected))throw new Error('Invalid saved runtime hostname');
    const deadline=Date.now()+45000;
    while(os.hostname()!==expected&&Date.now()<deadline)await new Promise(r=>setTimeout(r,500));
    if(os.hostname()!==expected)throw new Error('Runtime hostname identity was not restored; refusing to start QQ');
  }
}
// Existing containers and replacement containers use the same pinned fix.
// The helper is idempotent and rejects an unknown SDK instead of guessing.
if(process.platform==='linux'&&process.env.NAPCAT_RUNTIME_DIR){
  await promisify(execFile)(process.env.ASTRBOT_PYTHON||'python',['scripts/napcat_login_patch.py',path.resolve(process.env.NAPCAT_RUNTIME_DIR,'napcat/napcat.mjs')],{timeout:10000,windowsHide:true});
}
const db = await openDatabase(root, databaseUrl);
let key: Buffer;
try {
  key = await loadKey(root, {createIfMissing: !(await db.query('SELECT 1 FROM secrets UNION ALL SELECT 1 FROM bilibili_accounts UNION ALL SELECT 1 FROM bilibili_settings WHERE model_secret IS NOT NULL UNION ALL SELECT 1 FROM bilibili_logins WHERE secret<>\'\' LIMIT 1')).length});
} catch (error) {
  await db.close();
  throw error;
}
const agent = new NodeAgent(db, root, key);
const heartbeatFile=path.join(root,'agent-heartbeat.json');
const startedAt=new Date().toISOString();
const processStart=process.platform==='linux'?(await readFile('/proc/self/stat','utf8')).slice((await readFile('/proc/self/stat','utf8')).lastIndexOf(')')+1).trim().split(/\s+/)[19]:undefined;
const originalTick=agent.tick.bind(agent);
// One serialized heartbeat writer per process: overlapping ticks queue on a
// single chain, sequence numbers stay monotonic, temp files are unique per
// write and cleaned by their own writer, and a heartbeat left by a replaced
// process can never roll the freshness record back.
const heartbeat=new HeartbeatWriter(heartbeatFile,{pid:process.pid,processStart,startedAt});
agent.tick=async()=>{const busy=agent.busy;await originalTick();if(!busy&&!agent.closed)await heartbeat.write()};
try {
  await agent.begin();
} catch (error) {
  await db.close();
  throw error;
}
// Keep the independent runtime alive even when it currently owns no bots.
const keepAlive = setInterval(() => {}, 60_000);
let closing: Promise<void> | undefined;
function shutdown() {
  return closing ??= (async () => {
    clearInterval(keepAlive);
    try { await agent.shutdown(); } finally { try{await heartbeat.stop();await rm(heartbeatFile,{force:true})}finally{await db.close()} }
  })();
}
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => {
    void shutdown().then(() => process.exit(0), () => {
      console.error('Agent shutdown failed');
      process.exit(1);
    });
  });
}
