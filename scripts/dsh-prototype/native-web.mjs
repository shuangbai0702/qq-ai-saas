/** Reuses native DSH web/tool registration, never copies tool schemas by hand. */
import {createHash} from 'node:crypto';
export async function nativePublicWeb(native){
 const {Context}=await native.load('cordis'),ctx=new Context();
 try{
  for(const [name,config] of [['dsh-system-prompt',{includeHarnessIdentity:false,includeRuntimeContext:false}],['dsh-tools',{}],['dsh-web',{}],['dsh-web-fetch-http',{}],['dsh-tool-web',{search:false,fetch:true}]]){
   const m=await native.load(name);await ctx.plugin(m.default||m,config).await();
  }
  return {close:()=>ctx.fiber.dispose(),discover:async()=>ctx.tools.schemas().map(schema=>{
   const d=ctx.tools.get(schema.name),definition={name:d.name,description:d.description,parameters:d.parameters,output:{schema:d.output.schema}};
   return {id:d.name,version:createHash('sha256').update(JSON.stringify(definition)).digest('hex'),capability:'web_search',effect:'read',available:true,definition};
  }),execute:async(item,args,exec)=>{
   const result=await ctx.tools.execute({name:item.definition.name,arguments:args,callId:exec.callId,signal:exec.signal});
   if(result.isError)throw Error(result.error.message||'Native web request failed');return result.value;
  }};
 }catch(e){await ctx.fiber.dispose();throw e;}
}
