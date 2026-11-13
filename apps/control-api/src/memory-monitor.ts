import {SingleFlight} from '../../../packages/shared/single-flight.js';
import os from 'node:os';
import path from 'node:path';
import {readFile} from 'node:fs/promises';
import {execFile} from 'node:child_process';
export type MemorySample={sampledAt:string;hostAvailableBytes:number;apiRssBytes:number;apiHeapUsedBytes:number;cgroupCurrentBytes:number|null;cgroupLimitBytes:number|null;details?:any};
async function textFile(file:string){try{return(await readFile(file,'utf8')).trim()}catch{return null}}
async function numberFile(file:string){const text=await textFile(file);const value=Number(text);return text!==null&&text!==''&&Number.isSafeInteger(value)&&value>=0?value:null}
function counters(text:string|null){return Object.fromEntries((text||'').split('\n').flatMap(line=>{const [key,value]=line.trim().split(/\s+/);return key&&Number.isFinite(Number(value))?[[key,Number(value)]]:[]}))}
export async function collectMemory():Promise<MemorySample>{
 const usage=process.memoryUsage();const cg='/sys/fs/cgroup/';const[current,limit,stat,events,swap,swapMax,peak,pressure,hostPressure,meminfo,cpuPressure,cpuStat]=await Promise.all([numberFile(cg+'memory.current'),numberFile(cg+'memory.max'),textFile(cg+'memory.stat'),textFile(cg+'memory.events'),numberFile(cg+'memory.swap.current'),numberFile(cg+'memory.swap.max'),numberFile(cg+'memory.peak'),textFile(cg+'memory.pressure'),textFile('/proc/pressure/memory'),textFile('/proc/meminfo'),textFile('/proc/pressure/cpu'),textFile('/proc/stat')]);
 const host=Object.fromEntries((meminfo||'').split('\n').flatMap(line=>{const m=line.match(/^(\w+):\s+(\d+) kB/);return m?[[m[1],Number(m[2])*1024]]:[]}));
 const ticks=cpuStat?.match(/^cpu\s+(.+)$/m)?.[1].trim().split(/\s+/).map(Number).slice(0,8);
 const portableTicks=os.cpus().reduce((sum,cpu)=>({total:sum.total+Object.values(cpu.times).reduce((a,b)=>a+b,0),idle:sum.idle+cpu.times.idle}),{total:0,idle:0});
 return{sampledAt:new Date().toISOString(),hostAvailableBytes:host.MemAvailable??os.freemem(),apiRssBytes:usage.rss,apiHeapUsedBytes:usage.heapUsed,cgroupCurrentBytes:current,cgroupLimitBytes:limit,details:{host:{platform:os.platform(),totalBytes:host.MemTotal??os.totalmem(),freeBytes:os.freemem(),meminfoBytes:host,memoryPressure:hostPressure,cpuPressure,cpuTicks:ticks?{total:ticks.reduce((a,b)=>a+b,0),idle:ticks[3]+ticks[4]}:portableTicks,loadAverage:os.loadavg(),cpuCount:os.cpus().length,uptimeSeconds:os.uptime()},api:{pid:process.pid,...usage,uptimeSeconds:process.uptime(),cpu:process.cpuUsage()},cgroup:{currentBytes:current,limitBytes:limit,peakBytes:peak,swapCurrentBytes:swap,swapLimitBytes:swapMax,stat:counters(stat),events:counters(events),memoryPressure:pressure}}};
}
export async function readHostSnapshot(root:string){try{const file=await readFile(path.join(root,'monitor-host.json'),'utf8');if(file.length>1048576)return null;const value=JSON.parse(file);if(!Array.isArray(value.processes)||value.processes.length>512)return null;
 // API/Agent share a PID namespace. Fence the asynchronous host sample against
 // current /proc start ticks so exited/reused PIDs never linger or double count
 // across an API restart. PostgreSQL is in a different namespace.
 // Zombies have already exited and released their address space. Their /proc
 // entry can survive until the parent reaps it; it is not a running component.
 const rows=await Promise.all(value.processes.map(async(p:any)=>{if(p.container==='postgres'||!p.startTicks)return p;try{const stat=await readFile(`/proc/${p.namespacePid}/stat`,'utf8'),fields=stat.slice(stat.lastIndexOf(')')+1).trim().split(/\s+/);return fields[19]===String(p.startTicks)&&!['Z','X','x'].includes(fields[0])?p:null}catch(e:any){if(e.code!=='ENOENT'&&e.code!=='ESRCH')(value.errors??=[]).push({reason:'namespace-lifetime-unreadable',pid:p.pid});return null}}));value.processes=rows.filter(Boolean);return value}catch{return null}}
export function collectProcesses(root:string,python:string):Promise<any>{return new Promise(resolve=>{const child=execFile(python,[path.resolve('scripts/memory_snapshot.py')],{windowsHide:true,timeout:12000,maxBuffer:2*1024*1024},(error,stdout)=>{if(error)return resolve({processes:[],errors:[{reason:'process-collector-unavailable'}]});try{resolve(JSON.parse(stdout))}catch{resolve({processes:[],errors:[{reason:'process-collector-invalid-output'}]})}});child.stdin?.end(JSON.stringify({root,apiPid:process.pid}))})}
export function createMemoryMonitor(collect:()=>Promise<MemorySample>=collectMemory,intervalMs=30000,capacity=240){
 const history:MemorySample[]=[];const flight=new SingleFlight();let stopped=false;let lastError:string|null=null;
 const sample=()=>{if(stopped)return flight.pending||Promise.resolve();return flight.run(async()=>{try{const value=await collect();if(!stopped){const previous=history.at(-1);const a=previous?.details?.host?.cpuTicks,b=value.details?.host?.cpuTicks;if(a&&b&&b.total>a.total&&b.idle>=a.idle&&value.details?.view)value.details.view.overview.cpuPercent=Math.max(0,Math.min(100,100*(1-(b.idle-a.idle)/(b.total-a.total))));if(previous?.details?.view){previous.details={view:{overview:previous.details.view.overview,categories:previous.details.view.categories.map(({identity,...v}:any)=>v)}}}else if(previous?.details?.processSnapshot)delete previous.details.processSnapshot;history.push(value);if(history.length>capacity)history.splice(0,history.length-capacity);lastError=null}}catch{if(!stopped)lastError='采样失败'}});};
 const timer=setInterval(()=>void sample(),intervalMs);timer.unref();void sample();
 return{snapshot:()=>structuredClone(history),status:()=>({sampling:!!flight.pending,lastError}),sample,async close(){stopped=true;clearInterval(timer);await flight.pending;history.length=0}};
}
