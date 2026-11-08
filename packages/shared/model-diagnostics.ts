/** Node model callers append to the same per-bot diagnostic journal as Python.
 * No prompts, headers, URLs, credentials or raw errors enter this adapter. */
import {appendFileSync,renameSync,statSync,mkdirSync,readdirSync,unlinkSync} from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export type ModelOwner={root:string;botId:string;tenantId:string;generation?:number;revision?:number};
export async function observeModel<T>(owner:ModelOwner|undefined,phase:string,model:string,run:(trace:{
 request:(send:()=>Promise<Response>,signal?:AbortSignal)=>Promise<Response>;
 response:(data:any)=>void;adopted:(reason?:string)=>void;
})=>Promise<T>):Promise<T>{
 const attemptId=randomUUID(),started=performance.now();let wireIndex=0,headers=false,submission=false,httpStatus:number|undefined,status='failed',reason:string|undefined,adoption='not_adopted',adoptionReason='caller_did_not_adopt',usage:Record<string,unknown>={},activeSignal:AbortSignal|undefined;
 const safeModel=/^[\w.:/-]{1,128}$/.test(model)?model:undefined;
 const record=(row:Record<string,unknown>)=>{
  if(!owner||!owner.tenantId||!owner.root||!/^[\w-]{1,80}$/.test(owner.botId))return;
  try{
   const file=path.join(owner.root,'bots',owner.botId,'model-diagnostics.jsonl');
   mkdirSync(path.dirname(file),{recursive:true,mode:0o700});
   const configured=Number(process.env.FISHBOT_DIAGNOSTIC_ROTATE_BYTES||1024*1024);
   const limit=Number.isFinite(configured)?Math.max(1024,Math.min(16*1024*1024,configured)):1024*1024;
   try{if(statSync(file).size>=limit){
    renameSync(file,path.join(path.dirname(file),`model-diagnostics.previous.${String(Date.now()).padStart(13,'0')}.${process.pid}.${randomUUID()}.jsonl`));
    for(const old of readdirSync(path.dirname(file)).filter(n=>/^model-diagnostics\.previous\.[\d]+\.[\d]+\.[\w-]+\.jsonl$/.test(n)).sort().reverse().slice(3)){
     try{unlinkSync(path.join(path.dirname(file),old))}catch{}
    }
   }}catch{}
   appendFileSync(file,JSON.stringify({at:Date.now()/1000,pid:process.pid,botId:owner.botId,generation:owner.generation,revision:owner.revision,attemptId,phase,model:safeModel,...row})+'\n',{mode:0o600});
  }catch{/* Diagnostics never alter a request's result. */}
 };
 record({kind:'request_start'});
 try{
  const result=await run({
   request:async(send,signal)=>{
    activeSignal=signal;wireIndex++;submission=true;const index=wireIndex,at=performance.now();record({kind:'wire_request',wireIndex:index});
    try{const response=await send();headers=true;httpStatus=response.status;record({kind:'wire_result',wireIndex:index,status:'headers_received',httpStatus:response.status,durationMs:Math.round(performance.now()-at)});return response}
    catch(error){record({kind:'wire_result',wireIndex:index,status:signal?.aborted?'canceled':'unknown',reason:error instanceof Error?error.name:'Error',durationMs:Math.round(performance.now()-at)});throw error}
   },
   response:data=>{
    const number=(v:unknown)=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0?v:null;
    const u=data?.usage,cached=number(u?.prompt_tokens_details?.cached_tokens??u?.prompt_cache_hit_tokens);
    usage={inputTokens:number(u?.prompt_tokens),outputTokens:number(u?.completion_tokens),cachedTokens:cached,cacheUsageReported:cached!==null,
     responseId:typeof data?.id==='string'&&data.id.length<=256?data.id:undefined};
   },
   adopted:(why='applied')=>{adoption='adopted';adoptionReason=why}
  });status=httpStatus!==undefined&&httpStatus>=400?'failed':'completed';return result;
 }catch(error){reason=error instanceof Error?error.name:'Error';throw error}
 finally{
  record({kind:'request_result',status:activeSignal?.aborted&&status!=='completed'?'canceled':status,reason,httpStatus,wireCount:wireIndex,durationMs:Math.round(performance.now()-started),costStatus:'unknown',
   diagnosticDetail:status==='completed'?undefined:headers?'provider_response_received':submission?'request_submission_unknown':'request_not_sent',...usage});
  record({kind:'request_disposition',adoption,reason:adoptionReason});
 }
}
