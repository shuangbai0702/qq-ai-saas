// End-to-end check of a relay through this service's own egress.
//
// It uses the same code path the runtime uses — harness identity headers, DNS
// pinning, redirect refusal and the Responses bridge — so a pass here means the
// provider is reachable exactly as a Bot or a platform provider would reach it.
//
//   FISHBOT_RELAY_KEY=sk-... npx tsx scripts/relay-check.ts https://xingchiapi.com/v1 [model]
//
// The key is read from the environment and never printed.
import {modelHttp,modelResponsesWire} from '../packages/shared/model-http.js';
import {redact} from '../packages/shared/security.js';

const base=(process.argv[2]||'').replace(/\/+$/,''),model=process.argv[3]||'gpt-5.6',key=(process.env.FISHBOT_RELAY_KEY||'').trim();
if(!base||!key){console.error('usage: FISHBOT_RELAY_KEY=sk-... npx tsx scripts/relay-check.ts <base-url> [model]');process.exit(2)}
const origin=new URL(base).origin,listed=(process.env.FISHBOT_MODEL_RESPONSES_ORIGINS||'').split(',').map(value=>value.trim()).filter(Boolean);
if(!listed.includes(origin)){process.env.FISHBOT_MODEL_RESPONSES_ORIGINS=[...listed,origin].join(',');console.log('responses-wire: forced for '+origin)}
else console.log('responses-wire: enabled by FISHBOT_MODEL_RESPONSES_ORIGINS');
const headers={Authorization:'Bearer '+key,'Content-Type':'application/json'};

async function body(response:Response){const text=await response.text();try{return JSON.parse(text)}catch{return text.slice(0,600)}}

const models=await modelHttp(base+'/models',{headers:{Authorization:'Bearer '+key},signal:AbortSignal.timeout(20000)});
const listing:any=await body(models);
console.log('models:',models.status,models.ok&&Array.isArray(listing?.data)?`${listing.data.length} ids, ${model} ${listing.data.some((row:any)=>row?.id===model)?'listed':'NOT listed'}`:redact(JSON.stringify(listing).slice(0,300),[key]));

const usage=await modelHttp(base+'/usage',{headers:{Authorization:'Bearer '+key},signal:AbortSignal.timeout(20000)}).catch(()=>undefined);
if(usage)console.log('usage:',usage.status,redact(JSON.stringify(await body(usage)).slice(0,300),[key]));

const completion=await modelHttp(base+'/chat/completions',{method:'POST',headers,body:JSON.stringify({model,stream:false,max_tokens:32,messages:[{role:'system',content:'Reply with OK only.'},{role:'user',content:'OK'}]}),signal:AbortSignal.timeout(90000)});
const result:any=await body(completion);
if(completion.ok)console.log('completion:',completion.status,modelResponsesWire(new URL(base+'/chat/completions'))?'via /responses bridge':'native /chat/completions',redact(JSON.stringify(result?.choices?.[0]?.message?.content??'').slice(0,200),[key]));
else{console.log('completion:',completion.status,redact(JSON.stringify(result).slice(0,500),[key]));process.exitCode=1}
