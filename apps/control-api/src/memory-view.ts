// Only allowlisted operational metadata leaves the database. Never return health
// objects, process arguments, environment, model configuration or tokens.
export const categoryNames:Record<string,string>={api:'平台 API',postgres:'PostgreSQL',agent:'Agent',protocol:'NapCat / QQ',shared:'共享 AstrBot',dedicated:'独立 AstrBot',other:'其他 / 未归属'};
const numeric=(v:any)=>typeof v==='number'&&Number.isFinite(v)&&v>=0;
export function aggregate(rows:any[],complete=true){
 const unique=[...new Map(rows.map(p=>[`${p.container||'local'}:${p.pid}:${p.created}`,p])).values()];
 const sum=(key:string)=>complete&&unique.every(p=>numeric(p[key]))?unique.reduce((v,p)=>v+p[key],0):null;
 return{processCount:complete?unique.length:null,pssBytes:sum('pssBytes'),ussBytes:sum('ussBytes'),swapBytes:sum('swapBytes'),rssBytes:sum('rssBytes'),cpuSeconds:sum('cpuSeconds'),identity:unique.map(p=>`${p.container}:${p.pid}:${p.created}`).sort().join(',')};
}
export function buildMemoryView(sample:any,local:any,bots:any[],host:any,now=Date.now()){
 const hostFresh=host&&numeric(host.sampledAt)&&now-host.sampledAt*1000>=-5000&&now-host.sampledAt*1000<=90000;
 const localComplete=!local.truncated&&!(local.errors||[]).length;
 const localErrors=local.errors||[];
 const discoveryComplete=!local.truncated&&!localErrors.some((e:any)=>!e.ownerId&&e.kind!=='api');
 const hostComplete=!!hostFresh&&!host.truncated&&!(host.errors||[]).length;
 const localRows=local.processes||[];
 const processes:any[]=hostFresh?(host.processes||[]).map((p:any)=>{
   const owned=p.container!=='postgres'&&localRows.find((l:any)=>l.pid===p.namespacePid&&Math.abs(l.created-p.created)<.1);
   return{...p,ownerId:owned?.ownerId??p.ownerHint??null,kind:owned?.kind??p.kindHint??p.container};
 }):localRows.map((p:any)=>({...p,container:'local',namespacePid:p.pid}));
 // A newer runtime may not yet be in the asynchronous host sample. Keep it once,
 // but do not match a reused namespace PID without its creation time.
 if(hostFresh)for(const p of localRows)if(!processes.some(h=>h.container!=='postgres'&&h.namespacePid===p.pid&&Math.abs(h.created-p.created)<.1))processes.push({...p,container:'local',namespacePid:p.pid});
 const botIds=new Set(bots.map(b=>b.id));const groupIds=new Set(bots.map(b=>b.group_id).filter(Boolean));
 const category=(p:any)=>p.kind==='api'?'api':p.kind==='protocol'?'protocol':p.kind==='ai'?(groupIds.has(p.ownerId)||p.ownerId?.startsWith('group-')?'shared':'dedicated'):p.container==='postgres'?'postgres':p.kind==='agent'?'agent':p.container==='api'?'api':'other';
 for(const p of processes)p.category=category(p);
 const ownerComplete=(id:string,kind?:string)=>discoveryComplete&&!localErrors.some((e:any)=>e.ownerId===id&&(!kind||e.kind===kind));
 const categoryComplete=(id:string)=>{
   if(id==='postgres')return hostComplete;
   if(!discoveryComplete||hostFresh&&!hostComplete)return false;
   if(id==='agent')return hostComplete;
   return !localErrors.some((e:any)=>id==='api'?e.kind==='api':id==='protocol'?e.kind==='protocol':id==='shared'?e.kind==='ai'&&e.ownerId?.startsWith('group-'):id==='dedicated'?e.kind==='ai'&&!e.ownerId?.startsWith('group-'):false);
 };
 const categories=Object.entries(categoryNames).map(([id,name])=>({id,name,...aggregate(processes.filter(p=>p.category===id),categoryComplete(id))}));
 const accounts=bots.map(b=>{
   const protocolRows=processes.filter(p=>p.ownerId===b.id&&p.kind==='protocol');
   const dedicatedRows=processes.filter(p=>p.ownerId===b.id&&p.kind==='ai');
   const complete=ownerComplete(b.id);const count=complete?protocolRows.length+dedicatedRows.length:null;
   const healthTime=Date.parse(b.health?.sampledAt||b.heartbeat_at||'');
   const healthFresh=Number.isFinite(healthTime)&&now-healthTime<=90000&&now-healthTime>=-5000;
   const protocolComplete=ownerComplete(b.id,'protocol');
   const online=healthFresh&&b.health?.protocol?.online===true&&protocolRows.length>0&&protocolComplete&&b.desired_state==='running';
   const status=online?'online':b.desired_state==='offline'?(count===0?'evicted':'offline'):b.desired_state==='stopped'?'stopped':!protocolComplete?'unknown':b.observed_state==='starting'?'starting':!healthFresh?'stale':'offline';
   return{id:b.id,name:b.name,qq:b.qq_uin||b.expected_uin||null,protocol:b.protocol,runtimeMode:b.runtime_mode,groupId:b.group_id,status,desiredState:b.desired_state,observedState:b.observed_state,healthSampledAt:Number.isFinite(healthTime)?new Date(healthTime).toISOString():null,online,realOnline:online&&b.protocol!=='mock',residual:count===null?null:count>0&&!['running'].includes(b.desired_state),runtimeProcessCount:count,protocolMemory:aggregate(protocolRows,protocolComplete),dedicatedMemory:aggregate(dedicatedRows,ownerComplete(b.id,'ai'))};
 });
 const groups=[...new Set([...groupIds,...processes.filter(p=>p.category==='shared').map(p=>p.ownerId)])].filter(Boolean).map(id=>({id,configuredMembers:accounts.filter(b=>b.groupId===id).map(b=>({id:b.id,name:b.name,status:b.status,qq:b.qq})),onlineMemberCount:accounts.some(b=>b.groupId===id&&['stale','unknown'].includes(b.status))||!discoveryComplete?null:accounts.filter(b=>b.groupId===id&&b.online).length,onlineMembers:accounts.filter(b=>b.groupId===id&&b.online).map(b=>({id:b.id,name:b.name,qq:b.qq})),...aggregate(processes.filter(p=>p.category==='shared'&&p.ownerId===id),ownerComplete(id as string))}));
 const mem=sample.details.host.meminfoBytes||{};
 const platform=sample.details.host.platform||local.platform||host?.platform||'unknown';
 const portable=local.host||{};
 const sub=(a:any,b:any)=>numeric(a)&&numeric(b)?Math.max(0,a-b):null;
 const psi=(text:any,kind:string)=>{const m=typeof text==='string'&&text.match(new RegExp(`^${kind} avg10=([\\d.]+)`, 'm'));return m?Number(m[1]):null};
 const statusComplete=discoveryComplete&&accounts.every(b=>ownerComplete(b.id,'protocol')&&!['stale','unknown'].includes(b.status));
 const overview={platform,totalBytes:sample.details.host.totalBytes,usedBytes:sub(sample.details.host.totalBytes,sample.hostAvailableBytes),availableBytes:sample.hostAvailableBytes,swapUsedBytes:sub(mem.SwapTotal,mem.SwapFree)??(numeric(portable.swapUsedBytes)?portable.swapUsedBytes:null),swapTotalBytes:numeric(mem.SwapTotal)?mem.SwapTotal:numeric(portable.swapTotalBytes)?portable.swapTotalBytes:null,zswapPhysicalBytes:mem.Zswap??null,zswappedLogicalBytes:mem.Zswapped??null,onlineQQ:statusComplete?accounts.filter(b=>b.realOnline).length:null,onlineMock:statusComplete?accounts.filter(b=>b.online&&b.protocol==='mock').length:null,runtimeProcesses:localComplete?processes.filter(p=>['protocol','shared','dedicated'].includes(p.category)).length:null,memoryPressureSome:psi(sample.details.host.memoryPressure,'some'),memoryPressureFull:psi(sample.details.host.memoryPressure,'full'),cpuPressureSome:psi(sample.details.host.cpuPressure,'some'),cpuPercent:null as number|null};
 return{overview,categories,accounts,groups,processes,coverage:{hostFresh:!!hostFresh,hostSampledAt:host?.sampledAt?new Date(host.sampledAt*1000).toISOString():null,hostComplete,localComplete,complete:hostComplete&&localComplete,errors:[...(local.errors||[]),...(hostFresh?host.errors||[]:[{reason:'host-sample-missing-or-stale'}])],truncated:!!local.truncated||!!host?.truncated},unassignedRuntime:processes.filter(p=>p.ownerId&&!botIds.has(p.ownerId)&&!groupIds.has(p.ownerId)&&p.kind!=='api').map(p=>p.pid)};
}
