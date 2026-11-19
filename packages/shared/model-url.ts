// Pure input handling shared by forms, contracts and server-side discovery.
// Never guess a hostname or follow a redirect with a user's credential.
const bad=():never=>{throw Object.assign(new Error('无法识别模型地址，请粘贴服务商提供的 HTTP(S) Base URL；不要混入 Key 或登录凭证'),{statusCode:400})};
function cleanOne(value:string){
 let text=value.replace(/[\s'"`“”‘’<>()[\]{}，。；、]+$/g,'').replace(/^[\s'"`“”‘’<>()[\]{}]+/g,'');
 if(!/^https?:\/\//i.test(text))text='https://'+text;
 let url:URL;try{url=new URL(text)}catch{return bad()}
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return bad();
 // Tracking fragments are not part of an OpenAI endpoint. Query credentials
 // and arbitrary query parameters must not be silently removed or forwarded.
 for(const key of url.searchParams.keys())if(!/^(utm_.+|ref|from)$/i.test(key))return bad();
 url.search='';url.hash='';
 let path=url.pathname.replace(/\/{2,}/g,'/').replace(/\/+$/,'');
 for(let i=0;i<5;i++){
  const next=path.replace(/\/(chat\/completions|responses|models|embeddings)$/i,'').replace(/(\/(?:api\/)?v\d+)\1$/i,'$1');
  if(next===path)break;path=next;
 }
 return url.origin+path;
}
export function modelInputBases(value:string):string[]{
 if(typeof value!=='string'||!value.trim()||value.length>6000)return bad();
 let text=value.replace(/[\u200B-\u200D\uFEFF]/g,'').replace(/：/g,':').replace(/／/g,'/').replace(/．/g,'.').replace(/\\\//g,'/').replace(/\s*([:/\.])\s*/g,'$1').replace(/\b(https?)\/{2}/gi,'$1://').replace(/\b(https?):\/(?!\/)/gi,'$1://');
 const starts=[...text.matchAll(/https?:\/\//gi)].map(m=>m.index!);
 let parts:string[];
 if(starts.length){parts=starts.map((start,i)=>text.slice(start,starts[i+1]??text.length).split(/[\s<>"'`“”‘’，；、）)\]}]/,1)[0])}
 else{
  text=text.trim().replace(/^["'`\[（(]+|["'`\]）)]+$/g,'');
  // Missing scheme, including the common leading // copied from documentation.
  if(!/^(?:\/\/)?(?:localhost|[\w-]+(?:\.[\w-]+)+|\[[\da-f:]+\])(?::\d+)?(?:\/|$)/i.test(text))return bad();
  parts=text.split(/[\s，；、]+/).filter(Boolean).map(part=>part.replace(/^\/\//,''));
 }
 const bases:string[]=[];
 for(const part of parts){try{const base=cleanOne(part);if(!bases.includes(base))bases.push(base)}catch{if(parts.length===1)return bad()}}
 if(!bases.length)return bad();
 if(bases.length>3)throw Object.assign(new Error('粘贴的模型地址过多，请仅保留本次服务商地址'),{statusCode:400});
 return bases;
}
export function normalizeModelBaseUrl(value:string){return modelInputBases(value)[0]}
export function modelBaseCandidates(value:string){
 const bases=modelInputBases(value),result=[...bases];
 for(const base of bases){
  const url=new URL(base);
  if(/\/v\d+(?:beta\d*)?$/i.test(url.pathname))continue;
  for(const suffix of url.pathname.replace(/\/$/,'').endsWith('/api')?['/v1']:['/v1','/api/v1']){
   if(result.length>=6)break;const candidate=base+suffix;if(!result.includes(candidate))result.push(candidate);
  }
 }
 return result;
}
