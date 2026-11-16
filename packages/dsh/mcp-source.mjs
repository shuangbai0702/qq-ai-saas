/** Native MCP transport and registry. Operator allow-list grants public reads only. */
import {installedDsh} from './kernel.mjs';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import path from 'node:path';

export async function mcpSource({root,serverName,url,allowedReadTools}){
 const native=await installedDsh(root),{Context}=await native.load('cordis'),ctx=new Context();
 const packageRoot=native.root;
 const require=createRequire(path.join(packageRoot,'package.json')),Ajv=require('ajv');
 // No coercion, defaults, removal, remote refs or silently ignored schema keywords.
 const ajv=new Ajv({strict:true,allErrors:true}),validators=new Map(),allowed=new Set(allowedReadTools);
 try{
  for(const [pkg,config]of [['dsh-system-prompt',{includeHarnessIdentity:false,includeRuntimeContext:false}],['dsh-tools',{}],['dsh-mcp-client',{serverName,transport:'streamable-http',url,headers:{},toolCallTimeoutMs:20000,failOnStartupError:true,maxInstructionBytes:8192,reconnect:{enabled:false,initialDelayMs:1000,maxDelayMs:2000,maxAttempts:1}}]]){
   const module=await native.load(pkg);await ctx.plugin(module.default||module,config).await();
  }
  return {
   async discover(){return ctx.tools.schemas().filter(row=>allowed.has(row.name)).flatMap(row=>{
    const schema=JSON.stringify(row.parameters),version=createHash('sha256').update(JSON.stringify(row)).digest('hex');
    try{if(!validators.has(version))validators.set(version,ajv.compile(JSON.parse(schema)));}catch{return [];}
    return [{id:row.name,version,capability:'web_search',effect:'read',available:true,definition:{name:row.name,description:row.description,parameters:row.parameters,output:{schema:{}}}}];
   });},
   async validateArguments(item,args){const validate=validators.get(item.version);if(!validate||!validate(args))throw Error('MCP arguments do not match the current full JSON Schema');},
   async execute(item,args,exec){
    if(!allowed.has(item.id))throw Error('MCP public-read permission missing');
    const result=await ctx.tools.execute({name:item.id,arguments:args,callId:exec.callId,signal:exec.signal});
    if(result.isError)throw Error('Search MCP reported a tool failure');
    return result.value;
   },
   close:()=>ctx.fiber.dispose(),
  };
 }catch(error){await ctx.fiber.dispose();throw error;}
}
