/** Reuses native DSH web/tool registration, never copies tool schemas by hand. */
import {createHash} from 'node:crypto';
export async function nativePublicWeb(native,{search,reserveSearch}={}){
 const {Context}=await native.load('cordis'),ctx=new Context();
 try{
  for(const [name,config] of [['dsh-system-prompt',{includeHarnessIdentity:false,includeRuntimeContext:false}],['dsh-tools',{}],['dsh-web',{}],['dsh-web-fetch-http',{}]]){
   const m=await native.load(name);await ctx.plugin(m.default||m,config).await();
  }
  if(search){
   if(!reserveSearch)throw Error('Search budget reservation callback required');
   const {DeepSeekSearchProvider,DEEPSEEK_DEFAULT_API_VERSION}=await native.load('dsh-web-search-deepseek');
   const options=Object.freeze({...search,apiVersion:search.apiVersion||DEEPSEEK_DEFAULT_API_VERSION});
   const provider=new DeepSeekSearchProvider(()=>options),run=provider.search.bind(provider);
   provider.search=async(request,signal)=>{
    signal?.throwIfAborted();const reservation=await reserveSearch({request,model:options.model,maxTokens:options.maxTokens,maxUses:options.maxUses});
    if(!reservation)throw Error('Search budget was not reserved');
    let dispatched=false,completed=false;
    try{signal?.throwIfAborted();dispatched=true;const value=await run(request,signal);completed=true;return value;}
    finally{await reservation.settle({state:completed?'completed':dispatched?'unknown':'not_dispatched',usage:undefined});}
   };
   if(!provider.available())throw Error('Native search provider configuration is unavailable');
   ctx.web.registerSearchProvider(provider);
  }
  const tools=await native.load('dsh-tool-web');await ctx.plugin(tools.default||tools,{search:!!search,fetch:true}).await();
  return {close:()=>ctx.fiber.dispose(),discover:async()=>ctx.tools.schemas().map(schema=>{
   const d=ctx.tools.get(schema.name),definition={name:d.name,description:d.description,parameters:d.parameters,output:{schema:d.output.schema}};
   return {id:d.name,version:createHash('sha256').update(JSON.stringify(definition)).digest('hex'),capability:'web_search',effect:'read',available:true,definition};
  }),execute:async(item,args,exec)=>{
   const result=await ctx.tools.execute({name:item.definition.name,arguments:args,callId:exec.callId,signal:exec.signal});
   if(result.isError)throw Error(result.error.message||'Native web request failed');return result.value;
  }};
 }catch(e){await ctx.fiber.dispose();throw e;}
}
