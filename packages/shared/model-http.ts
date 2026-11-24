import {lookup} from 'node:dns/promises';import {BlockList,isIP} from 'node:net';import http from 'node:http';import https from 'node:https';import {Readable} from 'node:stream';
import {dshClientHeaders} from './model-attribution.js';
import {normalizeModelBaseUrl} from './model-url.js';
import {chatCompletionFromResponses,responsesRequest,responsesStreamToChat} from './model-wire.js';

const denied=new BlockList();
for(const [ip,bits] of [['0.0.0.0',8],['10.0.0.0',8],['100.64.0.0',10],['127.0.0.0',8],['169.254.0.0',16],['172.16.0.0',12],['192.0.0.0',24],['192.0.2.0',24],['192.88.99.0',24],['192.168.0.0',16],['198.18.0.0',15],['198.51.100.0',24],['203.0.113.0',24],['224.0.0.0',4],['240.0.0.0',4]] as const)denied.addSubnet(ip,bits,'ipv4');
const ipv6Global=new BlockList();ipv6Global.addSubnet('2000::',3,'ipv6');denied.addSubnet('2001::',23,'ipv6');denied.addSubnet('2001:db8::',32,'ipv6');denied.addSubnet('2002::',16,'ipv6');
export function publicModelIp(ip:string){const family=isIP(ip);return family===4?!denied.check(ip,'ipv4'):family===6&&ipv6Global.check(ip,'ipv6')&&!denied.check(ip,'ipv6')}
export function modelUrl(value:string,allowQuery=false){const url=new URL(value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password||(!allowQuery&&url.search)||url.hash)throw Object.assign(new Error('请使用不含账号、查询参数的 HTTP(S) Base URL'),{statusCode:400});return url}
export function modelCredentialDestination(value:string){
 if(!value)return '';
 const url=modelUrl(normalizeModelBaseUrl(value));
 return url.origin+url.pathname.replace(/\/+$/,'');
}
export function modelRequestHeaders(_url:URL,input?:HeadersInit){
 const headers=new Headers(input);
 // One client identity at the final egress boundary, including SDK overrides.
 // Provider-specific header injection is disabled; credentials stay untouched.
 for(const [name,value] of Object.entries(dshClientHeaders()))headers.set(name,value);
 return headers;
}
// A relay that refuses /chat/completions and only dispatches /responses is
// selected by exact origin, and only an operator can name it. The bridge keeps
// this service's single chat-completions contract on both sides.
export function modelResponsesWire(url:URL){
 if(!url.pathname.replace(/\/+$/,'').endsWith('/chat/completions'))return false;
 return (process.env.FISHBOT_MODEL_RESPONSES_ORIGINS||'').split(',').map(value=>value.trim()).filter(Boolean).includes(url.origin);
}
async function responsesWireResponse(response:http.IncomingMessage,model:string){
 const status=response.statusCode||200;
 if(/text\/event-stream/i.test(String(response.headers['content-type']||''))){
  const stream=(Readable.toWeb(response as any) as ReadableStream<Uint8Array>).pipeThrough(responsesStreamToChat(model));
  return new Response(stream as any,{status,headers:{'Content-Type':'text/event-stream; charset=utf-8'}});
 }
 const chunks:Buffer[]=[];let bytes=0;
 for await(const chunk of response){bytes+=(chunk as Buffer).length;if(bytes>8*1024*1024)throw new Error('模型响应超过安全大小');chunks.push(chunk as Buffer)}
 let parsed:any;try{parsed=JSON.parse(Buffer.concat(chunks).toString('utf8'))}catch{throw new Error('模型接口未返回兼容的 JSON')}
 return Response.json(chatCompletionFromResponses(parsed,model),{status});
}
export async function modelHttp(input:string|URL,init:RequestInit={},policy?:{allowQuery:boolean;publicOnly:boolean}){
 const url=modelUrl(String(input),policy?.allowQuery),host=url.hostname.replace(/^\[|\]$/g,'');
 const headers=modelRequestHeaders(url,init.headers);
 const wire=modelResponsesWire(url),target=new URL(url.href);
 let body=init.body as string|undefined,model='';
 if(wire&&typeof body==='string'&&body){
  const parsed=JSON.parse(body);
  model=typeof parsed?.model==='string'?parsed.model:'';
  body=JSON.stringify(responsesRequest(parsed));
  target.pathname=url.pathname.replace(/\/+$/,'').replace(/\/chat\/completions$/,'/responses');
 }
 // Only an operator may explicitly trust a private model service. No request or
 // Bot config can grant this exception. Tests opt in for their exact fixture port.
 const trusted=!policy?.publicOnly&&(process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS||'').split(',').map(x=>x.trim()).includes(url.origin);
 let timer:NodeJS.Timeout|undefined;const answers=await Promise.race([lookup(host,{all:true,verbatim:true}),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('模型地址解析超时')),10000);timer.unref()})]).finally(()=>clearTimeout(timer));
 if(!answers.length||(!trusted&&answers.some(a=>!publicModelIp(a.address))))throw Object.assign(new Error('模型地址不能访问本机、内网或保留地址'),{statusCode:400});
 init.signal?.throwIfAborted();const address=answers[0];
 return new Promise<Response>((resolve,reject)=>{
  const request=(target.protocol==='https:'?https:http).request(target,{method:init.method||'GET',headers:Object.fromEntries(headers),agent:false,
   // Pin the checked answer for this connection; TLS still verifies the original
   // hostname. A second DNS resolution cannot rebind a public host to the LAN.
   lookup:((_name:any,options:any,callback:any)=>options.all?callback(null,answers):callback(null,address.address,address.family)) as any},response=>{
    response.once('close',()=>init.signal?.removeEventListener('abort',abort));
    if((response.statusCode||0)>=300&&(response.statusCode||0)<400){response.destroy();reject(new Error('模型接口不允许重定向'));return}
    if(wire&&(response.statusCode||0)>=200&&(response.statusCode||0)<300){
     responsesWireResponse(response,model).then(resolve,error=>{response.destroy();reject(error)});
     return;
    }
    const headers=new Headers();for(const [name,value] of Object.entries(response.headers))if(value!==undefined)headers.set(name,Array.isArray(value)?value.join(','):value);
    try{const noBody=init.method==='HEAD'||[204,205].includes(response.statusCode||0);if(noBody)response.resume();resolve(new Response(noBody?null:Readable.toWeb(response) as any,{status:response.statusCode||502,headers}))}catch(error){response.destroy();reject(error)}
   });
  const abort=()=>request.destroy(new Error('模型请求已取消或超时'));init.signal?.addEventListener('abort',abort,{once:true});request.once('error',error=>{init.signal?.removeEventListener('abort',abort);reject(error)});
  if(init.signal?.aborted){abort();return}request.end(body);
 });
}
