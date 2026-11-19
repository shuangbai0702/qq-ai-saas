import {platformDisclaimer} from '../../../packages/shared/promotion';
import {useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';import {KeyRound,RefreshCw,Loader2} from 'lucide-react';import type {BotConfig} from '../../../packages/contracts/index';import {api} from './api';
import {defaultReasoningEffort,reasoningEfforts} from '../../../packages/shared/model-catalog-options';
import {normalizeModelBaseUrl} from '../../../packages/shared/model-url';
import {tokenCount,usePlatformBalance} from './referrals';
export function ModelFields({cfg,setCfg,apiKey,setKey,hasKey=false,botId}:{cfg:BotConfig;setCfg:(c:BotConfig)=>void;apiKey:string;setKey:(s:string)=>void;hasKey?:boolean;botId?:string}){
 const {data:balance,error:balanceError}=usePlatformBalance();
 const source=useMemo(()=>({baseUrl:cfg.baseUrl,apiKey,hasKey,botId}),[cfg.baseUrl,apiKey,hasKey,botId]);
 const request=useRef<AbortController|null>(null),edited=useRef(false),latestCfg=useRef(cfg);latestCfg.current=cfg;
 const [lookup,setLookup]=useState<{source:typeof source;loading:boolean;catalog?:any}>();
 useLayoutEffect(()=>()=>{request.current?.abort();request.current=null},[source]);
 const compatible=lookup&&lookup.source.apiKey===source.apiKey&&lookup.source.botId===source.botId&&lookup.source.hasKey===source.hasKey&&(lookup.source.baseUrl===source.baseUrl||lookup.catalog?.baseUrl===source.baseUrl);
 const current=compatible?lookup:undefined;
 const catalog=current?.catalog,loading=current?.loading===true;
 let validUrl=false;
 try{normalizeModelBaseUrl(cfg.baseUrl);validUrl=true}catch{}
 const canRefresh=validUrl&&!!(apiKey.trim()||(hasKey&&botId));
 useEffect(()=>{if(!edited.current||!canRefresh)return;const timer=setTimeout(()=>{edited.current=false;void refreshModels()},700);return()=>clearTimeout(timer)},[source,canRefresh]);
 async function refreshModels(){
  if(!canRefresh||request.current)return;
  const controller=new AbortController();
  request.current=controller;
  setLookup({source,loading:true});
  const stillCurrent=()=>request.current===controller&&!controller.signal.aborted;
  try{
   const result=await api('/model-catalog',{baseUrl:source.baseUrl,...(source.apiKey?{apiKey:source.apiKey}:{}),...(source.botId?{botId:source.botId}:{})},'POST',controller.signal);
   if(stillCurrent()){setLookup({source,loading:false,catalog:result});if(result.ok&&result.baseUrl&&result.baseUrl!==source.baseUrl)setCfg({...latestCfg.current,baseUrl:result.baseUrl})}
  }catch(e){
   if(stillCurrent())setLookup({source,loading:false,catalog:{models:[],message:(e as Error).message}});
  }finally{
   if(stillCurrent())request.current=null;
  }
 }
 const discoveryHint=!validUrl?'请输入有效的 API Base URL。':canRefresh?'点击刷新，获取可选模型。':'填写 API Key 后，即可刷新模型列表。';
 const selected=catalog?.models?.find((m:any)=>m.id===cfg.model),declared=selected?.reasoningEfforts as BotConfig['reasoningEffort'][]|undefined;
 const effortLabels:Record<string,string>={none:'不思考',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最高'};
 const choices=declared??(selected?.reasoningSupported===false?[]:reasoningEfforts);
 const sourceLabel=<><label className="field"><span>模型来源</span><select value={cfg.platformModel?'platform':'personal'} onChange={e=>setCfg({...cfg,platformModel:e.target.value==='platform'})}><option value="personal">自备模型</option><option value="platform" disabled={!cfg.platformModel&&(!balance?.enabled||!balance.granted)}>平台提供{balance?.enabled?` · 剩余 ${tokenCount(balance.available)} Token`:''}</option></select></label>{(!balance?.enabled||!balance.available||cfg.platformModel)&&<p className="muted">{balanceError?'暂时无法读取额度。':balance?.enabled?`剩余 ${tokenCount(balance.available)} Token。`:'平台额度暂不可用。'} <a href="#/activities">活动专区 · 领取额度</a> · <a href="#/referrals">邀请奖励</a></p>}</>;
 if(cfg.platformModel)return <>{sourceLabel}{balance?.campaign?.claimedAt&&<p className="muted">活动可用 {tokenCount(balance.campaign.available)} Token · 到期 {new Date(balance.campaign.expiresAt!).toLocaleString('zh-CN',{hour12:false})}。到期不影响邀请额度。</p>}<p className="platform-disclaimer">{platformDisclaimer}</p><p className="notice">这个工作区的机器人共用平台额度。额度不足以处理当前请求时，自动使用该机器人的已保存自备模型，由个人服务商计费。请先在「自备模型」中填写并保存 URL、模型和 Key，再切回平台提供；未配置时会提示补充。平台额度补充后会优先使用平台。</p></>;
 return <>{sourceLabel}<div className="form-grid"><label className="field"><span>模型供应商</span><select value={cfg.provider} onChange={e=>setCfg({...cfg,provider:e.target.value as any,baseUrl:e.target.value==='deepseek'?'https://api.deepseek.com/v1':e.target.value==='openai'?'https://api.openai.com/v1':cfg.baseUrl,model:e.target.value==='deepseek'?'deepseek-chat':e.target.value==='openai'?'gpt-4.1-mini':cfg.model,reasoningEffort:undefined})}><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="custom">自定义 OpenAI 兼容接口</option></select></label></div>
 <label className="field"><span>API Base URL</span><input type="text" inputMode="url" maxLength={6000} spellCheck={false} value={cfg.baseUrl} onChange={e=>{edited.current=true;setCfg({...cfg,baseUrl:e.target.value})}}/><small>可直接粘贴地址；填写 Key 后自动整理并探测接口，成功后自动回填。</small></label>
 <label className="field"><span>API Key</span><div className="input-icon"><KeyRound size={16}/><input aria-label="API Key" type="password" autoComplete="new-password" value={apiKey} onChange={e=>{edited.current=true;setKey(e.target.value)}} placeholder={hasKey?'已保存 · 输入新 Key 可替换':'sk-…（可稍后填写）'}/></div><small>{hasKey?'已加密保存。留空保留原 Key；任何页面均不回显原文。':'由你提供模型 Key，加密保存；模型用量由供应商计费。'}</small></label>
 <p className="model-setup-help">还没有 URL 和 Key？请先到你选择的模型服务商控制台，开通 API 并创建密钥。URL 填文档中的 OpenAI 兼容接口地址（通常以 /v1 结尾），不要填官网首页或聊天页面；Key 填该服务商生成的 API 密钥，不是账号密码。两者必须来自同一服务商；密钥不要分享给他人。填写后刷新下方模型列表，再选择模型。</p>
 <div className="model-discovery"><div className="model-discovery-status" role="status">{loading?<><Loader2 className="spin" size={14}/>正在获取可用模型…</>:catalog?.message||discoveryHint}</div><button type="button" className="secondary" disabled={loading||!canRefresh} onClick={()=>void refreshModels()}><RefreshCw size={13}/>刷新模型列表</button><label className="field"><span>选择模型</span><select aria-label="选择模型" disabled={loading||!catalog?.models?.length} value={cfg.model} onChange={e=>setCfg({...cfg,model:e.target.value,reasoningEffort:defaultReasoningEffort(catalog?.models?.find((m:any)=>m.id===e.target.value))})}>{!catalog?.models?.some((m:any)=>m.id===cfg.model)&&<option value={cfg.model}>当前：{cfg.model}（待列表核对）</option>}{catalog?.models?.map((m:any)=><option key={m.id} value={m.id}>{m.id}</option>)}</select><small>只读取供应商列表，不会自动切换已选模型或发起收费推理。获取失败时保留当前配置，可刷新重试。</small></label></div>
 <label className="field"><span>模型思考强度</span><select aria-label="模型思考强度" value={cfg.reasoningEffort??''} onChange={e=>{const next={...cfg};if(e.target.value)next.reasoningEffort=e.target.value as BotConfig['reasoningEffort'];else delete next.reasoningEffort;setCfg(next)}}><option value="">跟随供应商默认（不传参数）</option>{cfg.reasoningEffort&&!choices.includes(cfg.reasoningEffort)&&<option value={cfg.reasoningEffort}>当前：{effortLabels[cfg.reasoningEffort]} · 不在API声明范围</option>}{choices.map(effort=><option key={effort} value={effort}>{effortLabels[effort!]} · {effort}{declared?'':'（手动，未声明档位）'}</option>)}</select><small>{declared?.length?'API声明允许的档位：'+declared.map(v=>effortLabels[v!]).join('、')+'；声明不等于已通过实际推理验证。':selected?.reasoningSupported===false?'API声明不支持思考强度设置，请使用供应商默认。':selected?.reasoningSupported?'API声明支持推理，但未列出允许的强度；手动档位需以供应商文档和实际调用为准。':'API未提供思考强度能力信息；可保留默认，或按供应商文档手动设置。不会根据模型名称猜测支持范围。'}</small></label>
 <details className="advanced"><summary>高级：按用途选择模型</summary><p>共用上方接口和密钥；留空沿用默认模型，不影响主聊天模型。</p><div className="form-grid">{([['delegationModel','委托执行'],['auxiliaryModel','所有辅助判断'],['summaryModel','上下文压缩'],['favoriteModel','后台表情语义']] as const).map(([key,label])=><label className="field" key={key}><span>{label}</span><select aria-label={label+'模型'} value={cfg[key]??''} disabled={loading} onChange={e=>{const next={...cfg};if(e.target.value)next[key]=e.target.value;else delete next[key];setCfg(next)}}><option value="">默认：{cfg.model}</option>{cfg[key]&&!catalog?.models?.some((m:any)=>m.id===cfg[key])&&<option value={cfg[key]} disabled>当前：{cfg[key]}（待列表核对）</option>}{catalog?.models?.map((m:any)=><option key={m.id} value={m.id}>{m.id}</option>)}</select></label>)}</div></details></>;
}
