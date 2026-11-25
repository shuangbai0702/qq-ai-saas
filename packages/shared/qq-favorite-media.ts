import path from 'node:path';import {execFile} from 'node:child_process';
import {modelHttp} from './model-http.js';
const invalid=(message:string,statusCode=400)=>Object.assign(new Error(message),{statusCode});
export async function downloadFavorite(url:string,signal:AbortSignal){
 const u=new URL(url),host=u.hostname.toLowerCase();if(u.protocol!=='https:'||u.username||u.password||(u.port&&u.port!=='443')||!['qpic.cn','qq.com'].some(domain=>host===domain||host.endsWith('.'+domain)))throw invalid('收藏图片来源不受支持');
 // Signed CDN query strings are allowed, but model-service private-origin
 // exceptions never apply to media. The common transport pins checked DNS.
 const response=await modelHttp(u,{signal:AbortSignal.any([signal,AbortSignal.timeout(15000)])},{allowQuery:true,publicOnly:true});if(!response.ok||!response.body||Number(response.headers.get('content-length')||0)>8388608){await response.body?.cancel().catch(()=>{});throw invalid('收藏图片暂时不可用')}
 const blocks:Uint8Array[]=[];let length=0;try{for await(const block of response.body){length+=block.length;if(length>8388608)throw invalid('收藏图片超过8MiB');blocks.push(block)}}catch(e){await response.body.cancel().catch(()=>{});throw e}return Buffer.concat(blocks);
}
export async function normalize(input:string,destination:string,signal:AbortSignal){
 const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 return new Promise<{suffix:string;mime:string;animated:boolean}>((resolve,reject)=>execFile(python,[path.resolve('adapters/astrbot/favorite_media.py'),input,destination],{signal,timeout:15000,maxBuffer:8192,windowsHide:true,env:{PATH:process.env.PATH,SystemRoot:process.env.SystemRoot,TEMP:process.env.TEMP,TMP:process.env.TMP,PYTHONIOENCODING:'utf-8'}},(error,out)=>{if(error)return reject(invalid('收藏图片格式或尺寸不受支持'));try{resolve(JSON.parse(out))}catch{reject(invalid('收藏图片处理失败'))}}));
}
