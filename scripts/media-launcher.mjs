// Trusted executor only. Join the delegated cgroup before exec/spawn FFmpeg;
// the model never supplies executable, arguments, directory or cgroup path.
import {writeFile,readFile} from 'node:fs/promises';import path from 'node:path';import {spawn} from 'node:child_process';
const[root,id,exe,...args]=process.argv.slice(2);
if(process.platform!=='linux'||!path.isAbsolute(root)||root==='/sys/fs/cgroup'||!/^[-a-zA-Z0-9]{1,80}$/.test(id)||!path.isAbsolute(exe))throw new Error('Invalid media owner');
// The parent acquired this exact group using exclusive mkdir; it alone cleans
// it after joining the child. An existing group is never silently adopted.
const group=path.join(root,id);
await writeFile(path.join(group,'memory.high'),'201326592');await writeFile(path.join(group,'memory.max'),'max');await writeFile(path.join(group,'pids.max'),'64');await writeFile(path.join(group,'cpu.max'),'20000 100000');
await writeFile(path.join(group,'cgroup.procs'),String(process.pid));
if(!(await readFile(path.join(group,'cgroup.procs'),'utf8')).split(/\s+/).includes(String(process.pid)))throw new Error('Media cgroup join failed');
const child=spawn(exe,args,{stdio:'inherit',env:process.env});
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>child.kill(signal));
child.once('error',()=>{process.exitCode=1});child.once('close',(code)=>{process.exitCode=code??1});
