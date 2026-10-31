import {readFile,stat} from 'node:fs/promises';
import path from 'node:path';
const root=process.env.DATA_ROOT||'/var/lib/fishbot';
try {
  const pid=Number(await readFile(path.join(root,'agent.lock'),'utf8'));
  if(!Number.isInteger(pid)||pid<1)throw new Error('Invalid Agent PID');
  const args=(await readFile(`/proc/${pid}/cmdline`,'utf8')).split('\0').filter(Boolean);
  if(!/^node(?:js)?$/.test(path.basename(args[0]||''))||!args.some(arg=>/(?:^|\/)dist-server\/apps\/node-agent\/src\/main\.js$/.test(arg)))throw new Error('PID does not belong to the compiled independent Agent');
  process.kill(pid,0);
  const lease=await stat(path.join(root,'agent.lease'));
  const age=Date.now()-lease.mtimeMs;
  if(!lease.isDirectory()||age < -5000||age>20000)throw new Error('Agent lease heartbeat is stale');
  const heartbeat=JSON.parse(await readFile(path.join(root,'agent-heartbeat.json'),'utf8'));
  const processStat=await readFile(`/proc/${pid}/stat`,'utf8');
  const processStart=processStat.slice(processStat.lastIndexOf(')')+1).trim().split(/\s+/)[19];
  if(heartbeat.pid!==pid||heartbeat.processStart!==processStart||!Number.isFinite(heartbeat.at)||Date.now()-heartbeat.at>20000||heartbeat.at>Date.now()+5000)throw new Error('Agent business heartbeat is stale or belongs to another process');
} catch {
  process.exit(1);
}
