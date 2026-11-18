/** Local experiment: reuse DSH's installed pi-ai adapter, not another agent loop. */
import {createHash} from 'node:crypto';
import {installedDsh} from './kernel.mjs';

export const VISION_MAX_BYTES=4*1024*1024;

export async function nativeModel({baseURL,model,keyEnv='FISHBOT_DSH_TEST_API_KEY',maxTokens=8192,images}){
 if(!process.env[keyEnv])throw Error('Model credential environment variable is missing');
 const native=await installedDsh(),{Context}=await native.load('cordis'),ctx=new Context();
 try{
  const llm=await native.load('dsh-llm');await ctx.plugin(llm.default).await();
  const references=[],attachments=new Map();
  if(images){
   if(!Array.isArray(images)||!images.length||images.length>32)throw Error('Invalid image selection');
   let size=0,pixels=0;
   for(const image of images){
    const {mimeType,width,height,data}=image;
    if(!['image/png','image/jpeg','image/webp','image/gif'].includes(mimeType)||!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||width<1||height<1||width*height>16777216||typeof data!=='string')throw Error('Invalid original image');
    pixels+=width*height;
    if(pixels>16777216)throw Error('Image pixel budget exceeded');
    const bytes=Buffer.from(data,'base64');size+=bytes.length;
    if(!bytes.length||size>VISION_MAX_BYTES||bytes.toString('base64')!==data)throw Error('Invalid original image data');
    const attachmentId='sha256:'+createHash('sha256').update(bytes).digest('hex');
    const ref={attachmentId,name:'Image '+(references.length+1),width,height,mediaType:mimeType,bytes:bytes.length};
    attachments.set(attachmentId,{data:bytes,mediaType:mimeType,width,height,bytes:bytes.length});
    references.push({type:'image',attachment:ref});
   }
   // This per-delegation native attachment service holds authorized bytes
   // only during the request. It exposes no file path and writes no copy.
   ctx.provide('attachments',{
    imageHostPath:()=>undefined,
    readImageRequest(ref,target,signal){
     signal?.throwIfAborted();
     const image=attachments.get(ref.attachmentId);
     if(!image||image.width>target.width||image.height>target.height||image.bytes>target.maxBytes)throw Error('Original image cannot fit the model request');
     return image;
    },
   });
  }
  const pi=await native.load('dsh-llm-pi-ai');
  await ctx.plugin(pi,{providers:{'local-wire':{api:'openai-completions',baseURL,apiKeyEnv:keyEnv,
   ...(images?{requestImagePixelBudget:16777216,requestImageMaxBytes:VISION_MAX_BYTES,maxRequestImageBytes:6*1024*1024}:{}),
   models:[{id:model,contextWindow:128000,maxTokens,input:images?['text','image']:['text'],reasoningEfforts:false}],timeoutMs:45000,streamIdleTimeoutMs:45000}}}).await();
  return {images:references,stream:options=>ctx.llm.stream({...options,provider:'local-wire',model,maxTokens:Math.min(maxTokens,options.maxTokens??maxTokens)}),close:()=>ctx.fiber.dispose()};
 }catch(error){await ctx.fiber.dispose();throw error;}
}
