/** Protocol-owned extensions. No model, Journal or SaaS credential authority. */
import path from 'node:path';
import {readFile,lstat,realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {downloadFavorite} from '../../../packages/shared/qq-favorite-media.ts';

const md5=bytes=>createHash('md5').update(bytes).digest('hex');
const text=(value,max,name)=>{if(typeof value!=='string'||!value.trim()||value.length>max)throw Error('Invalid '+name);return value};
const checkResult=value=>{if(value?.retCode!==0)throw Error('QQ rejected favorite operation');return value};
export function qzoneForm(uin,content,visibility=4,targets=[]){
 if(![1,4,16,64,128].includes(visibility))throw Error('Invalid qzone visibility');
 if(!Array.isArray(targets)||targets.length>200||targets.some(x=>!/^\d{5,20}$/.test(String(x))))throw Error('Invalid qzone targets');
 if([16,128].includes(visibility)&&!targets.length)throw Error('Qzone targets required');
 const data=new URLSearchParams({syn_tweet_verson:'1',paramstr:'1',con:text(content,10000,'content'),feedversion:'1',ver:'1',ugc_right:String(visibility),to_sign:'0',hostuin:uin,code_version:'1',format:'json',qzreferrer:`https://user.qzone.qq.com/${uin}/main`});
 if(targets.length){data.set('allow_uins',targets.join('|'));data.set('who','1')}
 return data;
}
export function qzoneReceipt(value){
 if(!value||typeof value!=='object')throw Error('QQ空间未返回有效发表回执，结果未知');
 if(['code','subcode'].some(key=>value[key]!=null&&value[key]!==0))throw Object.assign(Error('QQ空间拒绝发表'),{qzoneRejected:true});
 const tid=value.t1_tid||value.tid;
 if(typeof tid!=='string'||!tid||tid.length>200)throw Error('QQ空间未返回有效发表回执，结果未知');
 return{tid};
}
async function responseBytes(response,max){
 if(!response.ok||!response.body||Number(response.headers.get('content-length')||0)>max){await response.body?.cancel();throw Error('QQ空间请求失败')}
 let length=0;const chunks=[];
 for await(const chunk of response.body){length+=chunk.length;if(length>max){await response.body.cancel().catch(()=>{});throw Error('QQ空间响应过大')}chunks.push(chunk)}
 return Buffer.concat(chunks);
}
export function installActions(actionMap,ctx,selfInfo,manifest,{fetcher=fetch,download=downloadFavorite}={}){
 const verified=new Map();let catalogFlight;
 const own=()=>{const uin=String(selfInfo.uin||'');if(!selfInfo.online||!/^\d{5,20}$/.test(uin)||manifest.expectedUin&&uin!==manifest.expectedUin)throw Error('QQ identity changed');return uin};
 const same=uin=>{if(own()!==uin)throw Error('QQ identity changed')};
 const details=async(params={})=>{
  const uin=own(),count=params.count??200;
  if(!Number.isInteger(count)||count<1||count>200)throw Error('Invalid favorite count');
  // Merge only the same account's concurrent read, never a write or action.
  if(!catalogFlight)catalogFlight=(async()=>{
   const ret=checkResult(await ctx.ntMsgApi.getCustomFaceList());same(uin);
   const entries=ret.emojiInfoList.slice(0,200);const present=new Set(entries.map(e=>String(e.emojiId)));
   for(const key of verified.keys())if(!present.has(key))verified.delete(key);
   let cursor=0;
   await Promise.all(Array.from({length:4},async()=>{
    for(;;){const row=entries[cursor++];if(!row)return;const id=String(row.emojiId);if(verified.has(id)){verified.get(id).url=row.url;continue}
     try{const bytes=await download(row.url,AbortSignal.timeout(10000));same(uin);verified.set(id,{resId:id,url:row.url,md5:md5(bytes)})}catch{same(uin);/* Unverified bytes never acquire a made-up digest. */}
    }
   }));
   same(uin);if(entries.some(e=>!verified.has(String(e.emojiId))))throw Error('Favorite bytes could not be verified; retry the read later');return entries.map(e=>verified.get(String(e.emojiId))).filter(Boolean);
  })().finally(()=>{catalogFlight=undefined});
  const rows=await catalogFlight;same(uin);return rows.slice(0,count);
 };
 const imageFile=async input=>{
  const file=path.resolve(text(input,4096,'image file')),root=await realpath(manifest.root);
  if(await realpath(file)!==file||!file.startsWith(root+path.sep))throw Error('Image must belong to this Bot');
  const stat=await lstat(file);if(!stat.isFile()||stat.isSymbolicLink()||stat.size>8388608||!stat.size)throw Error('Invalid image file');
  const bytes=await readFile(file);
  const image=bytes.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex'))||['GIF87a','GIF89a'].includes(bytes.subarray(0,6).toString())||bytes[0]===255&&bytes[1]===216&&bytes[2]===255||bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WEBP';
  if(bytes.length>8388608||!image)throw Error('Invalid image format');return{file,bytes};
 };
 const upload=async params=>{
  const uin=own(),{file,bytes}=await imageFile(params.file),digest=md5(bytes);same(uin);
  const result=checkResult(await ctx.ntMsgApi.addCustomFace(file));same(uin);
  const id=String(result.emojiId||'');if(!id)throw Error('QQ未返回有效收藏回执，结果未知');
  // The cache is proof of uploaded bytes, not proof of presence. details()
  // only exposes it when QQ's independently fetched current catalog contains it.
  if(id)verified.set(id,{resId:id,md5:digest});
  return{resId:id};
 };
 const signature=async params=>{
  const uin=own();if(typeof params.longNick!=='string'||params.longNick.length>1000)throw Error('Invalid signature');
  const res=await ctx.ntUserApi.modifySelfProfile({bio:params.longNick});same(uin);
  if(res.errorCode!==0)throw Error('QQ rejected signature');return null;
 };
 const publish=async params=>{
  // The whole operation has one deadline. A late native credential/read result
  // cannot start another upload or publish after the caller has stopped waiting.
  const budget=AbortSignal.timeout(45000);let stage='prepare';
  try{
  const uin=own(),form=qzoneForm(uin,params.content,params.ugc_right??4,params.target_uins??[]);
  if(params.images!==undefined&&(!Array.isArray(params.images)||params.images.length>9))throw Error('Invalid qzone images');
  const cookies=await ctx.ntWebApi.getCookies('qzone.qq.com');same(uin);
  const pskey=(await ctx.ntUserApi.getPSkey(['qzone.qq.com'])).get('qzone.qq.com');same(uin);
  if(!cookies.skey||!pskey)throw Error('QQ空间登录凭据尚未就绪');
  const cookie=`p_uin=o${uin}; p_skey=${pskey}; skey=${cookies.skey}; uin=o${uin}`;
  const gtk=ctx.ntWebApi.genBkn(cookies.skey);
  budget.throwIfAborted();
  if(params.images?.length){
   stage='upload';
   const rich=[];
   for(const input of params.images){
    const {bytes}=await imageFile(input);
    const data=new URLSearchParams({filename:'filename',uin,skey:cookies.skey,zzpaneluin:uin,p_uin:uin,p_skey:pskey,g_tk:String(gtk),base64:'1',picfile:bytes.toString('base64'),output_type:'jsonhtml',jsonhtml_callback:'callback',output_charset:'utf-8',albumtype:'7',exttype:'0',charset:'utf-8',upload_hd:'1',hd_width:'2048',hd_height:'10000',hd_quality:'96',uploadtype:'1',refer:'shuoshuo',backUrls:`http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,http://119.147.64.75/cgi-bin/upload/cgi_upload_image&url=https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk=${gtk}`,qzreferrer:`https://user.qzone.qq.com/${uin}/main`});
    same(uin);budget.throwIfAborted();
    const response=await fetcher(`https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk=${gtk}`,{method:'POST',redirect:'error',headers:{Cookie:cookie,'Content-Type':'application/x-www-form-urlencoded'},body:data,signal:AbortSignal.any([budget,AbortSignal.timeout(20000)])});
    const raw=(await responseBytes(response,1048576)).toString('utf8');same(uin);
    const begin=raw.indexOf('{'),end=raw.lastIndexOf('}');const result=JSON.parse(raw.slice(begin,end+1));
    const image=result.data;
    // QQ's jsonhtml upload endpoint returns {ret:0,data:...}; some deployments
    // return code instead. Neither a missing code nor image type 0 is failure.
    if(['code','ret'].some(key=>result[key]!=null&&result[key]!==0)||
       ![image?.albumid,image?.lloc].every(v=>typeof v==='string'&&v&&!/[,\t\r\n]/.test(v))||
       !Number.isInteger(image?.type)||image.type<0||![image?.height,image?.width].every(v=>Number.isInteger(v)&&v>0))throw Error('QQ空间图片未返回完整回执');
    rich.push(`,${image.albumid},${image.lloc},${image.lloc},${image.type},${image.height},${image.width},,${image.height},${image.width}`);
   }
   form.set('richtype','1');form.set('richval',rich.join('\t'));
  }
  same(uin);budget.throwIfAborted();stage='publish';
  const response=await fetcher(`https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6?g_tk=${gtk}`,{method:'POST',redirect:'error',headers:{Cookie:cookie,'Content-Type':'application/x-www-form-urlencoded'},body:form,signal:AbortSignal.any([budget,AbortSignal.timeout(20000)])});
  const bytes=await responseBytes(response,1048576);same(uin);return qzoneReceipt(JSON.parse(bytes.toString('utf8')));
  }catch(error){
   const status=stage==='publish'&&!error.qzoneRejected?'unknown':'rejected';
   throw Object.assign(Error(stage==='publish'?(status==='rejected'?'QQ空间明确拒绝发表':'QQ空间发表回执未知，请先核对，不要重复提交'):stage==='upload'?'QQ空间图片上传未完成，尚未提交说说':'QQ空间发布准备失败，尚未提交说说'),{qzone:{receipt:{status},code:'qzone_'+stage+'_failed',stage}});
  }
 };
 for(const [name,handler] of Object.entries({set_self_longnick:signature,fetch_custom_face_detail:details,add_custom_face:upload,send_qzone_msg:publish})){
  const action={actionName:name,async websocketHandle(params,echo){try{return{status:'ok',retcode:0,data:await handler(params??{}),echo}}catch(error){if(name==='send_qzone_msg'&&error.qzone)return{status:'failed',retcode:1200,data:error.qzone,message:error.message,echo};const reason=String(error?.message||'');const safe=/^(Invalid |Image must belong|QQ identity changed|QQ rejected |Favorite bytes could not be verified|QQ空间)/.test(reason)&&reason.length<160?reason+'；':'';return{status:'failed',retcode:1200,data:null,message:safe+'QQ操作失败或回执未确认，请查询状态；不要自动重复提交',echo}}},async handle(params){return this.websocketHandle(params,undefined)}};
  for(const suffix of ['','_async','_rate_limited'])actionMap.set(name+suffix,action);
 }
}
