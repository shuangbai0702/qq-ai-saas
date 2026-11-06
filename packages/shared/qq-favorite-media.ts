import {readFile} from 'node:fs/promises';import path from 'node:path';import {execFile} from 'node:child_process';
import {modelHttp} from './model-http.js';
type Bot={id:string;protocol:string};
const invalid=(message:string,statusCode=400)=>Object.assign(new Error(message),{statusCode});
export async function downloadFavorite(url:string,signal:AbortSignal){
 const u=new URL(url),host=u.hostname.toLowerCase();if(u.protocol!=='https:'||u.username||u.password||(u.port&&u.port!=='443')||!['qpic.cn','qq.com'].some(domain=>host===domain||host.endsWith('.'+domain)))throw invalid('收藏图片来源不受支持');
 // Signed CDN query strings are allowed, but model-service private-origin
 // exceptions never apply to media. The common transport pins checked DNS.
 const response=await modelHttp(u,{signal:AbortSignal.any([signal,AbortSignal.timeout(15000)])},{allowQuery:true,publicOnly:true});if(!response.ok||!response.body||Number(response.headers.get('content-length')||0)>8388608){await response.body?.cancel().catch(()=>{});throw invalid('收藏图片暂时不可用')}
 const blocks:Uint8Array[]=[];let length=0;try{for await(const block of response.body){length+=block.length;if(length>8388608)throw invalid('收藏图片超过8MiB');blocks.push(block)}}catch(e){await response.body.cancel().catch(()=>{});throw e}return Buffer.concat(blocks);
}
export async function nativeCall(root:string,bot:Bot,action:string,params:unknown,signal:AbortSignal){
 if(bot.protocol!=='napcat')throw invalid('当前协议端尚不支持QQ收藏同步',409);
 const m=JSON.parse(await readFile(path.join(root,'bots',bot.id,'napcat-platform.json'),'utf8'));if(m.botId!==bot.id||m.management.host!=='127.0.0.1'||!Number.isInteger(m.management.port))throw invalid('协议实例尚未就绪',409);
 const response=await fetch(`http://127.0.0.1:${m.management.port+1}/${action}`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+m.reverseWs.token},body:JSON.stringify(params),signal:AbortSignal.any([signal,AbortSignal.timeout(12000)])});
 const result:any=await response.json();if(!response.ok||result.status!=='ok'||result.retcode!==0)throw invalid('QQ收藏接口暂时不可用，请确认QQ在线',409);return result.data;
}
export async function normalize(input:string,destination:string,signal:AbortSignal){
 const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 return new Promise<{suffix:string;mime:string;animated:boolean}>((resolve,reject)=>execFile(python,[path.resolve('adapters/astrbot/favorite_media.py'),input,destination],{signal,timeout:15000,maxBuffer:8192,windowsHide:true,env:{PATH:process.env.PATH,SystemRoot:process.env.SystemRoot,TEMP:process.env.TEMP,TMP:process.env.TMP,PYTHONIOENCODING:'utf-8'}},(error,out)=>{if(error)return reject(invalid('收藏图片格式或尺寸不受支持'));try{resolve(JSON.parse(out))}catch{reject(invalid('收藏图片处理失败'))}}));
}
