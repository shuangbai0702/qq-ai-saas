import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {readHostSnapshot} from '../apps/control-api/src/memory-monitor.js';

test('host snapshot excludes a real reaping-pending zombie while preserving live and other-namespace rows', {skip:process.platform!=='linux',timeout:10000}, async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-host-lifetime-'));
 const child=spawn('python3',['-u','-c',"import os,sys\npid=os.fork()\nif pid==0: os._exit(0)\nprint(pid,flush=True)\nsys.stdin.read()\nos.waitpid(pid,0)"],{stdio:['pipe','pipe','pipe']});
 const exited=once(child,'exit');
 try{
  const pid=await new Promise<number>((resolve,reject)=>{let text='';child.once('error',reject);child.stdout.on('data',b=>{text+=b.toString();if(text.includes('\n'))resolve(Number(text.trim()))})});
  assert.ok(pid>0);
  const fields=async(n:number)=>(await readFile(`/proc/${n}/stat`,'utf8')).split(')').slice(1).join(')').trim().split(/\s+/);
  let dead=await fields(pid);for(let i=0;dead[0]!=='Z'&&i<100;i++){await delay(10);dead=await fields(pid)}assert.equal(dead[0],'Z');
  const live=await fields(process.pid),row=(namespacePid:number,startTicks:string,container='agent')=>({pid:namespacePid,namespacePid,startTicks,container,pssBytes:null});
  await writeFile(path.join(root,'monitor-host.json'),JSON.stringify({sampledAt:Date.now()/1000,processes:[row(pid,dead[19]),{...row(process.pid,live[19]),pssBytes:123},row(pid,dead[19],'postgres'),row(process.pid,'0')],errors:[]}));
  const result=await readHostSnapshot(root);assert.ok(result);
  assert.deepEqual(result.processes.map((p:any)=>[p.namespacePid,p.container]),[[process.pid,'agent'],[pid,'postgres']]);
  assert.equal(result.processes[0].pssBytes,123);assert.equal(result.processes[1].pssBytes,null);assert.deepEqual(result.errors,[]);
 }finally{child.stdin.end();await exited;await rm(root,{recursive:true,force:true})}
});
