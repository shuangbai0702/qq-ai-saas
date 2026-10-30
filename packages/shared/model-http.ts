import {lookup} from 'node:dns/promises';import {BlockList,isIP} from 'node:net';import http from 'node:http';import https from 'node:https';import {Readable} from 'node:stream';

const denied=new BlockList();
for(const [ip,bits] of [['0.0.0.0',8],['10.0.0.0',8],['100.64.0.0',10],['127.0.0.0',8],['169.254.0.0',16],['172.16.0.0',12],['192.0.0.0',24],['192.0.2.0',24],['192.88.99.0',24],['192.168.0.0',16],['198.18.0.0',15],['198.51.100.0',24],['203.0.113.0',24],['224.0.0.0',4],['240.0.0.0',4]] as const)denied.addSubnet(ip,bits,'ipv4');
const ipv6Global=new BlockList();ipv6Global.addSubnet('2000::',3,'ipv6');denied.addSubnet('2001::',23,'ipv6');denied.addSubnet('2001:db8::',32,'ipv6');denied.addSubnet('2002::',16,'ipv6');
export function publicModelIp(ip:string){const family=isIP(ip);return family===4?!denied.check(ip,'ipv4'):family===6&&ipv6Global.check(ip,'ipv6')&&!denied.check(ip,'ipv6')}
export function modelUrl(value:string){const url=new URL(value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw Object.assign(new Error('请使用不含账号、查询参数的 HTTP(S) Base URL'),{statusCode:400});return url}
export async function modelHttp(input:string|URL,init:RequestInit={}){
 const url=modelUrl(String(input)),host=url.hostname.replace(/^\[|\]$/g,'');
 // Only an operator may explicitly trust a private model service. No request or
 // Bot config can grant this exception. Tests opt in for their exact fixture port.
 const trusted=(process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS||'').split(',').map(x=>x.trim()).includes(url.origin);
 let timer:NodeJS.Timeout|undefined;const answers=await Promise.race([lookup(host,{all:true,verbatim:true}),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('模型地址解析超时')),10000);timer.unref()})]).finally(()=>clearTimeout(timer));
 if(!answers.length||(!trusted&&answers.some(a=>!publicModelIp(a.address))))throw Object.assign(new Error('模型地址不能访问本机、内网或保留地址'),{statusCode:400});
 init.signal?.throwIfAborted();const address=answers[0];
 return new Promise<Response>((resolve,reject)=>{
  const request=(url.protocol==='https:'?https:http).request(url,{method:init.method||'GET',headers:Object.fromEntries(new Headers(init.headers)),agent:false,
   // Pin the checked answer for this connection; TLS still verifies the original
   // hostname. A second DNS resolution cannot rebind a public host to the LAN.
   lookup:((_name:any,options:any,callback:any)=>options.all?callback(null,answers):callback(null,address.address,address.family)) as any},response=>{
    response.once('close',()=>init.signal?.removeEventListener('abort',abort));
    if((response.statusCode||0)>=300&&(response.statusCode||0)<400){response.destroy();reject(new Error('模型接口不允许重定向'));return}
    const headers=new Headers();for(const [name,value] of Object.entries(response.headers))if(value!==undefined)headers.set(name,Array.isArray(value)?value.join(','):value);
    try{const noBody=init.method==='HEAD'||[204,205].includes(response.statusCode||0);if(noBody)response.resume();resolve(new Response(noBody?null:Readable.toWeb(response) as any,{status:response.statusCode||502,headers}))}catch(error){response.destroy();reject(error)}
   });
  const abort=()=>request.destroy(new Error('模型请求已取消或超时'));init.signal?.addEventListener('abort',abort,{once:true});request.once('error',error=>{init.signal?.removeEventListener('abort',abort);reject(error)});
  if(init.signal?.aborted){abort();return}request.end(init.body as string|undefined);
 });
}
