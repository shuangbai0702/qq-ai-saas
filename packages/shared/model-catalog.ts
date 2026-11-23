import {modelHttp} from './model-http.js';
import {reasoningEfforts} from './model-catalog-options.js';
import {modelBaseCandidates,normalizeModelBaseUrl} from './model-url.js';
import type {BotConfig} from '../contracts/index.js';
type Effort=typeof reasoningEfforts[number];
// Provider declarations are metadata, not a successful inference test. Never
// infer an effort enum from a model's name or a bare `reasoning` capability.
export function advertisedReasoning(row:any):{reasoningEfforts?:Effort[];reasoningSupported?:boolean}{
 const raw=row?.supported_reasoning_efforts??row?.reasoning_efforts??row?.reasoning?.efforts??row?.capabilities?.reasoning_efforts;
 if(Array.isArray(raw)){
  const values=reasoningEfforts.filter(value=>raw.includes(value));
  if(values.length)return{reasoningEfforts:values,reasoningSupported:true};
  if(!raw.length)return{reasoningEfforts:[],reasoningSupported:false};
 }
 const supported=row?.supports_reasoning??row?.capabilities?.reasoning;
 if(typeof supported==='boolean')return{reasoningSupported:supported};
 if(Array.isArray(row?.supported_parameters)&&row.supported_parameters.some((p:unknown)=>p==='reasoning'||p==='reasoning_effort'))return{reasoningSupported:true};
 return{};
}
// A bounded models GET only; no billed completion probes or SDK retries.
async function discoverOne(baseUrl:string,key:string,bounded:AbortSignal,transport:typeof modelHttp){
 const checkedAt=new Date().toISOString(),url=baseUrl+'/models';
 const failure=(message:string,status?:number)=>({ok:false,models:[],message,status,checkedAt,capabilitiesVerified:false});
 try{
  const response=await transport(url,{headers:{Authorization:'Bearer '+key,Accept:'application/json'},redirect:'error',signal:bounded});
  if(!response.ok){await response.body?.cancel();return failure(response.status===401||response.status===403?'供应商拒绝了此 Key，请检查权限，获取成功后才能选择模型。':response.status===404?'供应商未提供 /models 列表，请检查接口后重试。':'模型列表暂时不可用，请稍后重试后重新获取。',response.status)}
  const reader=response.body?.getReader();if(!reader)return failure('接口未返回模型列表。');let bytes=0,text='';const decoder=new TextDecoder();
  try{while(true){const chunk=await reader.read();if(chunk.done)break;bytes+=chunk.value.byteLength;if(bytes>256000)return failure('模型列表超过安全大小，请检查接口后重试。');text+=decoder.decode(chunk.value,{stream:true})}text+=decoder.decode()}finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
  let data:any;try{data=JSON.parse(text)}catch{return failure('接口未返回兼容的 JSON 模型列表，请检查接口后重试。')}
  if(!Array.isArray(data?.data))return failure('供应商的列表格式不兼容，请检查接口后重试。');
  const seen=new Set<string>(),models:Array<{id:string;reasoningEfforts?:Effort[];reasoningSupported?:boolean}>=[];for(const row of data.data){if(models.length>=300)break;const id=row?.id;if(typeof id!=='string'||!id.trim()||id.length>150||/[\x00-\x1f]/.test(id)||id.includes(key)||seen.has(id))continue;seen.add(id);models.push({id,...advertisedReasoning(row)})}
  return{ok:true,baseUrl,models,message:models.length?`发现 ${models.length} 个模型；列表不代表所有能力均已验证。`:'此 Key 的模型列表为空，请检查 Key 的模型权限。',checkedAt,capabilitiesVerified:false};
 }catch{return failure(bounded.aborted?'模型列表请求已取消或超时，请检查接口后重试。':'无法连接模型列表接口，请检查 URL 后重新获取。')}
}
// A single bounded discovery operation: candidates run concurrently and only
// issue models GETs. Input order decides ties, rather than response speed.
export async function discoverModels(baseUrl:string,key:string,signal?:AbortSignal,transport=modelHttp){
 const candidates=modelBaseCandidates(baseUrl),timeout=AbortSignal.timeout(10000),bounded=signal?AbortSignal.any([signal,timeout]):timeout;
 if(key.length>4&&candidates.some(base=>base.includes(key)))throw Object.assign(new Error('请将 API Key 填到密钥栏，不要放进 Base URL'),{statusCode:400});
 const results=await Promise.all(candidates.map(base=>discoverOne(base,key,bounded,transport)));
 const result=results.find(r=>r.ok)??results.find(r=>'status' in r&&(r.status===401||r.status===403))??results[0];
 return{...result,candidatesChecked:results.length};
}
export async function resolveModelBaseUrl(raw:string,key:string,signal?:AbortSignal,transport=modelHttp){
 const canonical=normalizeModelBaseUrl(raw);
 if(!key||modelBaseCandidates(raw).length===1)return canonical;
 const result=await discoverModels(raw,key,signal,transport);
 return result.ok&&'baseUrl' in result?result.baseUrl!:canonical;
}

const selectionFields=['platformModel','baseUrl','model','auxiliaryModel','delegationModel','summaryModel','favoriteModel'] as const;
export function modelSelectionChanged(previous:BotConfig,next:BotConfig){return selectionFields.some(field=>previous[field]!==next[field])}

/** Validate changed selections against this credential's actual models GET.
 * Unchanged saves stay local; no catalogue cache or inference probe is created. */
export async function validateModelSelection(config:BotConfig,key:string,raw=config.baseUrl,transport=modelHttp){
 if(config.platformModel)return config.baseUrl; // Platform connection is resolved by its owner, not these personal fields.
 const models=[config.model,config.auxiliaryModel,config.delegationModel,config.summaryModel,config.favoriteModel].filter((id):id is string=>!!id);
 if(!models.length)return config.baseUrl; // An unconfigured QQ-only bot is valid.
 const fail=(message:string):never=>{throw Object.assign(new Error(message),{statusCode:400})};
 if(!config.model)fail('请先从 API 模型列表选择主模型');
 if(!key)fail('请填写 API Key，并从该接口的模型列表选择模型');
 const result=await discoverModels(raw,key,undefined,transport);
 if(!result.ok||!('baseUrl'in result))fail('无法获取该 API Key 的模型列表，请刷新模型列表后重试');
 const available=new Set(result.models.map(row=>row.id));
 if(models.some(id=>!available.has(id)))fail('所选模型不在该 API Key 返回的模型列表中，请刷新后重新选择');
 return 'baseUrl'in result?result.baseUrl!:fail('模型列表未返回有效接口地址');
}
