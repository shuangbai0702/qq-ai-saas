export class ApiError extends Error{
 constructor(message:string,public status:number){super(message);this.name='ApiError'}
}
export async function api(url:string,body?:unknown,method?:string,signal?:AbortSignal){
 const verb=method||(body?'POST':'GET'),writing=!['GET','HEAD'].includes(verb);
 // Model diagnostics runs up to six bounded upstream probes in sequence.
 const timeout=AbortSignal.timeout(url.endsWith('/model-probe')?160000:30000),requestSignal=signal?AbortSignal.any([signal,timeout]):timeout;
 let r:Response;
 try{r=await fetch('/v1'+url,{method:verb,credentials:'include',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:requestSignal})}
 catch(error){
  if(signal?.aborted)throw error;
  throw new ApiError((timeout.aborted?'请求超时':'网络连接失败')+(writing?'，操作可能已提交，请先刷新状态，勿重复操作。':'，请检查网络后重试。'),0);
 }
 let d:any;
 try{d=await r.json()}catch{
  throw new ApiError(r.status>=500?'服务暂时不可用，请稍后刷新状态。':'服务器返回异常，请刷新状态后重试。',r.status);
 }
 if(!r.ok){
  if(r.status===429){const seconds=Number(r.headers.get('Retry-After'));throw new ApiError('操作较频繁，'+(Number.isFinite(seconds)&&seconds>0&&seconds<=3600?'请在 '+Math.ceil(seconds)+' 秒后重试。':'请稍后重试。')+'请勿连续提交。',429)}
  if(r.status===401&&!url.startsWith('/auth/'))throw new ApiError('登录已失效，请先备份未保存的编辑，再刷新页面重新登录。',401);
  throw new ApiError(r.status>=502?'服务暂时不可用，请稍后刷新状态。':d?.error||'请求失败，请刷新状态后重试。',r.status);
 }
 return d;
}
