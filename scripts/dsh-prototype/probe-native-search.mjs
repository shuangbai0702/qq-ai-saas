/** Explicit one-search local probe. Credentials come only from the launching environment. */
import {installedDsh} from './kernel.mjs';
import {nativePublicWeb} from './native-web.mjs';
const apiKey=process.env.FISHBOT_DSH_TEST_API_KEY,baseURL=process.env.FISHBOT_DSH_TEST_BASE_URL;
if(!apiKey||!baseURL)throw Error('Set test API key and trusted base URL in the environment');
const model=process.env.FISHBOT_DSH_TEST_MODEL||'deepseek-v4.1-flash';
const native=await installedDsh();let reservations=0,settlement;
const web=await nativePublicWeb(native,{search:{apiKey,baseURL,model,maxTokens:1024,maxUses:1},reserveSearch:async()=>{
 if(reservations++)throw Error('Local probe search limit reached');return{settle:async value=>{settlement=value;}};
}});
try{
 const tool=(await web.discover()).find(v=>v.id==='web_search');if(!tool)throw Error('Native search tool unavailable');
 const result=await web.execute(tool,{queries:['DeepSeek official API documentation']},{callId:'local-native-search',signal:AbortSignal.timeout(45000)});
 console.log(JSON.stringify({nativeProvider:'deepseek-official',model,success:true,sources:result.sources.map(v=>({url:v.url,title:v.title})),settlement,usageKnown:false}));
}catch(error){console.log(JSON.stringify({nativeProvider:'deepseek-official',model,success:false,error:String(error.message).replaceAll(apiKey,'[REDACTED]'),settlement,usageKnown:false}));process.exitCode=1;}
finally{await web.close();}
