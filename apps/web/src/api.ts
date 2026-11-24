export class ApiError extends Error{
 constructor(message:string,public status:number){super(message);this.name='ApiError'}
}
export async function api(url:string,body?:unknown,method?:string,signal?:AbortSignal){
 const verb=method||(body!==undefined?'POST':'GET'),writing=!['GET','HEAD'].includes(verb);
 const controller=new AbortController();let timedOut=false;
 const cancel=()=>controller.abort();
 if(signal?.aborted)cancel();else signal?.addEventListener('abort',cancel,{once:true});
 const timer=setTimeout(()=>{timedOut=true;controller.abort()},url.endsWith('/model-probe')?160000:30000);
 const networkError=()=>new ApiError((timedOut?'请求超时':'网络连接失败')+(writing?'，操作可能已提交，请先刷新状态，勿重复操作。':'，请检查网络后重试。'),0);
 try{
  let r:Response;
  try{r=await fetch('/v1'+url,{method:verb,credentials:'include',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal})}
  catch(error){if(signal?.aborted)throw error;throw networkError()}
  let d:any;
  try{d=await r.json()}catch(error){
   if(signal?.aborted)throw error;
   if(timedOut)throw networkError();
   throw new ApiError(r.status>=500?'服务暂时不可用，请稍后刷新状态。':'服务器返回异常，请刷新状态后重试。',r.status);
  }
  if(!r.ok){
   if(r.status===429){const seconds=Number(r.headers.get('Retry-After'));throw new ApiError('操作较频繁，'+(Number.isFinite(seconds)&&seconds>0&&seconds<=3600?'请在 '+Math.ceil(seconds)+' 秒后重试。':'请稍后重试。')+'请勿连续提交。',429)}
   if(r.status===401&&!url.startsWith('/auth/'))throw new ApiError('登录已失效，请先备份未保存的编辑，再刷新页面重新登录。',401);
   throw new ApiError(r.status>=502?'服务暂时不可用，请稍后刷新状态。':d?.error||'请求失败，请刷新状态后重试。',r.status);
  }
  return d;
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel)}
}
