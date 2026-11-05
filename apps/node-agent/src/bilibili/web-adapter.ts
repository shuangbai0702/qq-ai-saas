import {createHash} from 'node:crypto';
export type Credential={cookies:Record<string,string>;refreshToken:string};
export type BiliFailureSource={endpoint:string;method:'GET'|'POST';httpStatus?:number;platformCode?:number};
export class BiliError extends Error {constructor(public code:string,message:string,public ambiguous=false,public source?:BiliFailureSource,public usage?:Record<string,unknown>){super(message)}}
export async function boundedJson(response:Response,max=2*1024*1024){
 if(!response.body)throw new BiliError('invalid_response','平台返回空响应');
 const chunks:Uint8Array[]=[];let size=0;const reader=response.body.getReader();
 try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>max)throw new BiliError('response_too_large','响应超过允许大小');chunks.push(value)}return JSON.parse(Buffer.concat(chunks,size).toString('utf8'))}
 finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
}
const mixin=[46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52];
export function wbi(params:Record<string,string>,img:string,sub:string,now=Date.now()){
 const src=img.split('/').pop()!.split('.')[0]+sub.split('/').pop()!.split('.')[0],key=mixin.map(i=>src[i]||'').join('').slice(0,32);
 const p={...params,wts:String(Math.floor(now/1000))};const q=Object.keys(p).sort().map(k=>encodeURIComponent(k)+'='+encodeURIComponent(p[k as keyof typeof p].replace(/[!'()*]/g,''))).join('&');return q+'&w_rid='+createHash('md5').update(q+key).digest('hex');
}
export function cookieCandidate(headers:Headers,refreshToken:string):Credential{
 const cookies:Record<string,string>={};for(const line of headers.getSetCookie()){
  const match=/^(SESSDATA|bili_jct|DedeUserID)=([^;\r\n]+)/.exec(line);if(match){if(cookies[match[1]])throw new BiliError('invalid_credential','登录凭证重复');cookies[match[1]]=match[2]}
 }
 if(!cookies.SESSDATA||!cookies.bili_jct||!/^\d+$/.test(cookies.DedeUserID||'')||Object.values(cookies).some(v=>v.length>8192||/[\r\n;]/.test(v)))throw new BiliError('invalid_credential','未取得完整登录凭证');
 return {cookies,refreshToken};
}
export class BiliWeb {
 private signingKeys=new Map<string,{img:string;sub:string;expires:number}>();
 constructor(public transport:typeof fetch=fetch){}
 async request(endpoint:string,params:Record<string,any>={},credential?:Credential,signal?:AbortSignal,method:'GET'|'POST'='GET',json=false){
  const url=new URL(endpoint);if(!['api.bilibili.com','passport.bilibili.com','api.vc.bilibili.com','message.bilibili.com'].includes(url.hostname)||url.protocol!=='https:')throw new BiliError('invalid_endpoint','接口地址不允许');
  const headers:Record<string,string>={'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/','Accept':'application/json'};
  if(credential)headers.Cookie=Object.entries(credential.cookies).map(([k,v])=>k+'='+v).join('; ');
  const fields=Object.fromEntries(Object.entries(params).filter(([,v])=>v!==undefined).map(([k,v])=>[k,String(v)]));
  let body:string|undefined;if(method==='GET')for(const[k,v]of Object.entries(fields))url.searchParams.set(k,v);
  else{if(!credential?.cookies.bili_jct)throw new BiliError('auth_required','请先绑定B站账号');headers['Content-Type']=json?'application/json':'application/x-www-form-urlencoded';body=json?JSON.stringify(params):new URLSearchParams({...fields,csrf:credential.cookies.bili_jct,csrf_token:credential.cookies.bili_jct}).toString()}
  const controller=AbortSignal.any([signal??new AbortController().signal,AbortSignal.timeout(15000)]);
  let response:Response;try{response=await this.transport(url,{method,headers,body,signal:controller,redirect:'error'})}catch{throw new BiliError(method==='POST'?'write_outcome_unknown':'network_unavailable',method==='POST'?'请求结果未确认，禁止重复提交':'平台网络请求失败',method==='POST')}
  if(!response.ok){await response.body?.cancel();throw new BiliError([412,429].includes(response.status)?'risk_control':'http_error',[412,429].includes(response.status)?'平台风控或限流，已暂停请求':'平台请求失败',method==='POST'&&response.status>=500,{endpoint:url.pathname,method,httpStatus:response.status})}
  let payload:any;try{payload=await boundedJson(response)}catch{throw new BiliError('invalid_response','平台响应无法核实',method==='POST')}
  if(payload.code!==0)throw new BiliError([-101,-111].includes(payload.code)?'auth_required':[-352,-412,-509].includes(payload.code)?'risk_control':'platform_rejected','平台拒绝请求，请检查账号状态与操作权限',false,{endpoint:url.pathname,method,httpStatus:response.status,...(typeof payload.code==='number'?{platformCode:payload.code}:{})});
  return {data:payload.data,headers:response.headers};
 }
 async get(path:string,args:Record<string,any>={},credential?:Credential,signal?:AbortSignal){return (await this.request('https://api.bilibili.com'+path,args,credential,signal)).data}
 async self(credential:Credential,signal?:AbortSignal){const data=await this.get('/x/web-interface/nav',{},credential,signal);if(!data?.isLogin||String(data.mid)!==credential.cookies.DedeUserID)throw new BiliError('identity_mismatch','登录身份验证失败');return{uid:String(data.mid),label:String(data.uname||data.mid).slice(0,100),coinBalance:typeof data.money==='number'&&Number.isFinite(data.money)&&data.money>=0?data.money:null}}
 async signed(path:string,args:Record<string,string>,credential?:Credential,signal?:AbortSignal){
  signal?.throwIfAborted();
  // Cache signing material only, never account responses or in-flight Work.
  // Credential rotation selects a new bucket; no raw cookies are retained here.
  const identity=credential?createHash('sha256').update(JSON.stringify([credential.cookies.DedeUserID,credential.cookies.SESSDATA])).digest('hex'):'anonymous';
  for(const [id,key]of this.signingKeys)if(key.expires<=Date.now())this.signingKeys.delete(id);
  let key=this.signingKeys.get(identity);
  if(!key){
   const nav=await this.get('/x/web-interface/nav',{},credential,signal);
   signal?.throwIfAborted();
   const img=nav?.wbi_img?.img_url,sub=nav?.wbi_img?.sub_url;
   if(![img,sub].every(v=>typeof v==='string'&&/^https:\/\/(?:[a-z0-9-]+\.)*hdslb\.com\/[^?#]*\/[a-f0-9]{32}\.png$/i.test(v)))throw new BiliError('wbi_unavailable','平台签名信息不可用');
   key={img,sub,expires:Date.now()+5*60*1000};
   if(this.signingKeys.size>=8)this.signingKeys.delete(this.signingKeys.keys().next().value!);
   this.signingKeys.set(identity,key);
  }
  signal?.throwIfAborted();
  return(await this.request('https://api.bilibili.com'+path+'?'+wbi(args,key.img,key.sub),{},credential,signal)).data;
 }
 async video(bvid:string,c?:Credential,signal?:AbortSignal){return this.get('/x/web-interface/view',{bvid},c,signal)}
 async resolveVideo(value:string,signal:AbortSignal){
  let url=new URL(value);const seen=new Set<string>();
  for(let i=0;i<4;i++){
   if(url.protocol!=='https:'||url.username||url.password||url.port)throw new BiliError('invalid_link','视频链接地址不允许');
   const match=['www.bilibili.com','bilibili.com'].includes(url.hostname)&&/^\/video\/(BV[A-Za-z0-9]{10})(?:\/|$)/.exec(url.pathname);
   if(match)return{bvid:match[1],url:'https://www.bilibili.com/video/'+match[1],meaning:'仅解析视频身份，尚未观看内容'};
   if(url.hostname!=='b23.tv'||!/^\/[A-Za-z0-9]+\/?$/.test(url.pathname)||seen.has(url.href))throw new BiliError('invalid_link','只解析B站视频链接与b23.tv短链，不访问其他地址');
   seen.add(url.href);
   const response=await this.transport(url,{redirect:'manual',signal:AbortSignal.any([signal,AbortSignal.timeout(10000)]),headers:{'User-Agent':'Mozilla/5.0'}});
   await response.body?.cancel();
   if([412,429].includes(response.status))throw new BiliError('risk_control','短链解析被平台风控或限流',false,{endpoint:url.pathname,method:'GET',httpStatus:response.status});
   const location=response.headers.get('location');if(![301,302,303,307,308].includes(response.status)||!location)throw new BiliError('link_unresolved','平台未返回可核实的视频跳转');
   url=new URL(location,url);
  }
  throw new BiliError('link_unresolved','短链跳转次数超过限制');
 }
 async transcript(bvid:string,part:number,language:string,c?:Credential,signal?:AbortSignal){
  const video=await this.video(bvid,c,signal),p=video.pages?.find((p:any)=>p.page===part);if(!p)throw new BiliError('part_missing','视频分P不存在');
  if(video.bvid!==bvid||!Number.isSafeInteger(video.aid)||video.aid<=0||!Number.isSafeInteger(p.cid)||p.cid<=0||!Number.isFinite(p.duration)||p.duration<=0)throw new BiliError('subtitle_identity','视频和分P身份无法核实');
  // The legacy unsigned endpoint can return unrelated subtitle URLs even
  // while echoing the requested BV/CID. Never fall back to that response.
  const player=await this.signed('/x/player/wbi/v2',{aid:String(video.aid),cid:String(p.cid)},c,signal);
  if(player?.bvid!==bvid||player?.aid!==video.aid||player?.cid!==p.cid)throw new BiliError('subtitle_identity','字幕播放器与目标视频身份不匹配');
  const available=player?.subtitle?.subtitles||[];
  const sub=available.find((s:any)=>s.lan===language)||available.find((s:any)=>String(s.lan).includes('zh'))||available[0];
  if(!sub)return{modality:'subtitles',bvid,cid:p.cid,part,segments:[],coveredRanges:[],limitations:['平台未提供可用字幕；未分析音频或画面']};
  if(typeof sub.subtitle_url!=='string'||!sub.subtitle_url)throw new BiliError('subtitle_unavailable','平台未提供有效字幕地址，不能改用未核实的字幕');
  const url=new URL(sub.subtitle_url.startsWith('//')?'https:'+sub.subtitle_url:sub.subtitle_url);
  if(url.protocol!=='https:'||url.username||url.password||url.port&&url.port!=='443'||url.hash||!/(^|\.)hdslb\.com$/.test(url.hostname))throw new BiliError('invalid_subtitle_source','字幕来源不允许');
  const response=await this.transport(url,{headers:{'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/'},signal:AbortSignal.any([signal??new AbortController().signal,AbortSignal.timeout(15000)]),redirect:'error'});
  if(!response.ok){await response.body?.cancel();throw new BiliError('subtitle_unavailable','字幕读取失败')}
  const raw=await boundedJson(response);if(!Array.isArray(raw.body))throw new BiliError('invalid_subtitles','字幕格式无效');
  const segments=raw.body.slice(0,1000).map((v:any)=>({from:Number(v.from),to:Number(v.to),text:String(v.content).slice(0,600)}));
  if(segments.some((v:any,i:number)=>!Number.isFinite(v.from)||!Number.isFinite(v.to)||v.from<0||v.to<v.from||v.to>Number(p.duration)+5||i>0&&v.from<segments[i-1].from))throw new BiliError('subtitle_integrity','字幕时间轴不匹配，不能作为可靠内容');
  return{modality:'subtitles',bvid,cid:p.cid,part,duration:p.duration,language:sub.lan,aiGenerated:String(sub.lan).startsWith('ai-')||!!sub.ai_type,source:url.origin+url.pathname,binding:{aid:video.aid,bvid,cid:p.cid,endpoint:'/x/player/wbi/v2',subtitleId:typeof sub.id_str==='string'?sub.id_str:Number.isSafeInteger(sub.id)?String(sub.id):null},segments,coveredRanges:segments.map((v:any)=>[v.from,v.to]),limitations:['仅字幕文字，未观看画面、未分析声音','已核对签名播放器身份与时间轴；不保证字幕转写和翻译完全准确',...(raw.body.length>1000?['字幕已截断']:[])]};
 }
}
