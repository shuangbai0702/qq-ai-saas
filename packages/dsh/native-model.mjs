/** Local experiment: reuse DSH's installed pi-ai adapter, not another agent loop. */
import {installedDsh} from './kernel.mjs';

export async function nativeModel({baseURL,model,keyEnv='FISHBOT_DSH_TEST_API_KEY',maxTokens=8192}){
 if(!process.env[keyEnv])throw Error('Model credential environment variable is missing');
 const native=await installedDsh(),{Context}=await native.load('cordis'),ctx=new Context();
 try{
  const llm=await native.load('dsh-llm');await ctx.plugin(llm.default).await();
  const pi=await native.load('dsh-llm-pi-ai');
  await ctx.plugin(pi,{providers:{'local-wire':{api:'openai-completions',baseURL,apiKeyEnv:keyEnv,models:[{id:model,contextWindow:128000,maxTokens,input:['text'],reasoningEfforts:false}],timeoutMs:45000,streamIdleTimeoutMs:45000}}}).await();
  return {stream:options=>ctx.llm.stream({...options,provider:'local-wire',model,maxTokens:Math.min(maxTokens,options.maxTokens??maxTokens)}),close:()=>ctx.fiber.dispose()};
 }catch(error){await ctx.fiber.dispose();throw error;}
}
