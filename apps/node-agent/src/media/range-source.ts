/** Task-owned loopback bridge. FFmpeg never receives an untrusted network URL.
 * Only a fixed, DNS-pinned Bili CDN object can be read; all reads share a budget. */
import {createServer} from 'node:http';
import {request} from 'node:https';
import {lookup} from 'node:dns/promises';
import {randomBytes} from 'node:crypto';
import {pipeline} from 'node:stream/promises';
import {Transform} from 'node:stream';
import {mediaUrl,publicAddress} from './lifecycle.js';
import {BiliError} from '../bilibili/web-adapter.js';

export async function rangeSource<T>(source:string,budget:{remaining:number;received:number},signal:AbortSignal,run:(url:string)=>Promise<T>,io:{lookup?:typeof lookup;request?:typeof request}={}):Promise<T>{
 const route='/'+randomBytes(24).toString('hex');let failure:unknown;
 const controller=new AbortController(),owned=AbortSignal.any([signal,controller.signal]);
 const open=async(value:string,range:string,redirects=0):Promise<import('node:http').IncomingMessage>=>{
  owned.throwIfAborted();const url=mediaUrl(value),addresses=await (io.lookup||lookup)(url.hostname,{all:true});
  if(!addresses.length||addresses.some(a=>!publicAddress(a.address)))throw new BiliError('private_media_address','媒体域名解析到非公网地址');
  owned.throwIfAborted();
  const response=await new Promise<import('node:http').IncomingMessage>((resolve,reject)=>{
   const req=(io.request||request)(url,{signal:owned,headers:{Range:range,Referer:'https://www.bilibili.com/','User-Agent':'Mozilla/5.0'},lookup:(_h,o:any,cb:any)=>o.all?cb(null,addresses):cb(null,addresses[0].address,addresses[0].family)},resolve);req.on('error',reject);req.end();
  });
  if([301,302,303,307,308].includes(response.statusCode||0)){
   response.destroy();if(redirects>=3||!response.headers.location)throw new BiliError('media_redirect','媒体跳转超过限制');
   return open(new URL(response.headers.location,url).href,range,redirects+1);
  }
  const received=/^bytes (\d+)-(\d+)\/(\d+)$/.exec(String(response.headers['content-range'])),asked=/^bytes=(\d+)-(\d+)$/.exec(range)!;
  if(response.statusCode!==206||!received||Number(received[1])!==Number(asked[1])||Number(received[2])>Number(asked[2])||Number(received[2])<Number(received[1])||Number(received[2])>=Number(received[3])||Number(response.headers['content-length'])!==Number(received[2])-Number(received[1])+1){response.destroy();throw new BiliError('media_range_required','来源不支持可核实的Range；拒绝退回整流下载')}
  return response;
 };
 const pending=new Set<Promise<void>>();
 const server=createServer((req,res)=>{
  const task=(async()=>{
   if(req.url!==route||req.method!=='GET'){res.writeHead(404).end();return}
   const match=/^bytes=(\d+)-(\d*)$/.exec(req.headers.range||'bytes=0-');
   if(!match)throw new BiliError('invalid_range','媒体范围无效');
   const start=Number(match[1]),requestedEnd=match[2]?Number(match[2]):Infinity;
   if(!Number.isSafeInteger(start)||requestedEnd<start||budget.remaining<=0)throw new BiliError('media_too_large','媒体累计网络额度不足');
   // Bound each request as well as the total. FFmpeg follows Content-Range.
   const end=Math.min(requestedEnd,start+Math.min(1048576,budget.remaining)-1);
   const upstream=await open(source,`bytes=${start}-${end}`);
   const cancel=()=>upstream.destroy();res.once('close',cancel);
   res.writeHead(206,{'Content-Type':'video/mp4','Accept-Ranges':'bytes','Content-Range':upstream.headers['content-range']!,...(upstream.headers['content-length']?{'Content-Length':upstream.headers['content-length']}:{})});
   try{await pipeline(upstream,new Transform({transform(chunk,_encoding,done){budget.remaining-=chunk.length;budget.received+=chunk.length;if(budget.remaining<0)done(new BiliError('media_too_large','媒体超过累计网络额度'));else done(null,chunk)}}),res,{signal:owned})}finally{res.off('close',cancel);upstream.destroy()}
  })().catch(error=>{if(!owned.aborted&&!res.destroyed){failure??=error;res.destroy(error as Error)}});
  pending.add(task);void task.finally(()=>pending.delete(task));
 });
 await new Promise<void>((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)});
 try{const result=await run(`http://127.0.0.1:${(server.address() as any).port}${route}`);if(failure)throw failure;return result}
 catch(error){throw failure??error}
 finally{controller.abort();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));await Promise.allSettled([...pending])}
}
