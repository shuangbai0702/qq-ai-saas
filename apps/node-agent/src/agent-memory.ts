import {getHeapStatistics} from 'node:v8';

/** Only the independent Agent, before DB leases or protocol pipes are owned. */
export function agentHeapArgs(args:string[],value=process.env.FISHBOT_AGENT_HEAP_MIB){
 const target=value===undefined?192:Number(value);
 if(!Number.isInteger(target)||target<128||target>384)throw new Error('FISHBOT_AGENT_HEAP_MIB must be an integer from 128 to 384');
 const existing=args.find(a=>/^--max[-_]old[-_]space[-_]size=/.test(a));
 const size=existing?Number(existing.split('=')[1]):undefined;
 // Preserve an explicitly larger operational allowance, unless overridden.
 const desired=value===undefined&&size!==undefined?Math.max(target,size):target;
 if(size===desired)return args;
 return [...args.filter(a=>!/^--max[-_]old[-_]space[-_]size=/.test(a)),`--max-old-space-size=${desired}`];
}

export function prepareAgentHeap(){
 const args=agentHeapArgs(process.execArgv);
 if(process.platform==='linux'&&args!==process.execArgv){
  if(typeof process.execve!=='function')throw new Error('Agent heap startup requires Node >=22.15 with execve, or an explicit matching heap flag');
  // execve keeps the supervisor-owned PID. No runtime resources exist yet.
  process.execve(process.execPath,[process.execPath,...args,...process.argv.slice(1)],process.env as Record<string,string>);
 }
 return getHeapStatistics().heap_size_limit;
}
