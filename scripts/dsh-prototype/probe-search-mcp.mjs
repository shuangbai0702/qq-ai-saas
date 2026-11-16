/** Explicit public read-only probe using the installed DSH MCP client; no global profile changes. */
import {installedDsh} from '../../packages/dsh/kernel.mjs';
const native=await installedDsh(),{Context}=await native.load('cordis'),ctx=new Context();
try{
 const modules=[['dsh-system-prompt',{includeHarnessIdentity:false,includeRuntimeContext:false}],['dsh-tools',{}],['dsh-mcp-client',{serverName:'exa',transport:'streamable-http',url:'https://mcp.exa.ai/mcp',headers:{},toolCallTimeoutMs:20000,failOnStartupError:true,maxInstructionBytes:8192,reconnect:{enabled:false,initialDelayMs:1000,maxDelayMs:2000,maxAttempts:1}}]];
 for(const [pkg,config]of modules){const m=await native.load(pkg);await ctx.plugin(m.default||m,config).await();}
 const tools=ctx.tools.schemas(),tool=tools.find(v=>v.name==='mcp__exa__web_search_exa');
 if(!tool)throw Error('Public search tool not advertised; do not guess a replacement');
 const query=process.env.FISHBOT_DSH_MCP_TEST_QUERY||'site:api-docs.deepseek.com web tools DeepSeek';
 const args={query,numResults:2,objective:'Find official DeepSeek API documentation describing web search tools; prefer primary documentation and return source URLs.'};
 const result=await ctx.tools.execute({name:tool.name,arguments:args,callId:'explicit-public-search',signal:AbortSignal.timeout(25000)});
 if(result.isError)throw Error(result.error?.message||'MCP search failed');
 const text=result.value.content.filter(v=>v.type==='text').map(v=>v.text).join('');
 const urls=[...text.matchAll(/^URL:\s*(https?:\/\/\S+)/gm)].map(v=>v[1]);
 console.log(JSON.stringify({nativeDshVersion:native.version,transport:'streamable-http',server:'https://mcp.exa.ai/mcp',tools:tools.map(v=>v.name),searchSucceeded:urls.length>0,urls,returnedCharacters:text.length,modelCalls:0,apiKeyUsed:false,globalConfigChanged:false}));
 if(!urls.length)process.exitCode=1;
}catch(error){console.log(JSON.stringify({success:false,error:error.message,cause:error.cause?.message}));process.exitCode=1;}finally{await ctx.fiber.dispose();}
