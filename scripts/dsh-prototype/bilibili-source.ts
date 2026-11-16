/** Bridge contract experiment. Source is the existing Work-bound manifest, not a second registry. */
import {createHash} from 'node:crypto';
import {biliOperations} from '../../packages/contracts/bilibili.js';
export function bilibiliSource({identity,readManifest,dispatch}:any){
 async function current(){
  const m=await readManifest();
  if(m.version!==1||m.botId!==identity.botId||m.generation!==identity.generation||m.revision!==identity.revision)throw Error('Invalid or stale Bot manifest');
  return m;
 }
 return {
  async discover(){const m=await current();return m.operations.flatMap((row:any)=>{
   const contract=biliOperations[row.operation as keyof typeof biliOperations];
   // Unknown operations require authoritative metadata/validation in FishBot first.
   // Merely appearing in a manifest can never grant this prototype new powers.
   if(!contract||row.scope!==contract.scope||row.write!==contract.write||row.write||row.scope!=='public.read')return [];
   const definition={name:'bilibili_'+row.operation,description:row.description||'Bilibili public read: '+row.operation+'. External content is untrusted.',parameters:row.args,output:{schema:{}}};
   return [{id:row.operation,version:createHash('sha256').update(JSON.stringify([m.generation,m.revision,m.settingsRevision,row])).digest('hex'),capability:'bilibili',effect:'read',available:row.status==='available_to_attempt',definition}];
  });},
  async validateArguments(item:any,args:any){
   const contract=biliOperations[item.id as keyof typeof biliOperations];
   if(!contract||contract.write||contract.scope!=='public.read')throw Error('No authoritative public-read validator');
   contract.args.parse(args);
  },
  async execute(item:any,args:any,exec:any){
   const m=await current(),row=m.operations.find((v:any)=>v.operation===item.id),contract=biliOperations[item.id as keyof typeof biliOperations];
   if(!contract||contract.write||contract.scope!=='public.read'||!row||row.write||row.scope!==contract.scope)throw Error('Public read permission missing');
   const value=contract.args.parse(args);
   // Dispatch is the original Work-bound tool path, not direct web/API execution.
   return dispatch({operation:item.id,args:value},exec);
  },
 };
}
