import {mkdir,writeFile,rename,rm} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export async function renameAtomic(source:string,target:string,move:typeof rename=rename){
 for(let attempt=0;;attempt++){
  try{await move(source,target);return}catch(error:any){
   if(process.platform!=='win32'||!['EPERM','EACCES','EBUSY'].includes(error.code)||attempt>=4)throw error;
   await new Promise(resolve=>setTimeout(resolve,25*(attempt+1)));
  }
 }
}
export async function atomicJson(file:string,value:unknown){
 await mkdir(path.dirname(file),{recursive:true});
 const temporary=file+'.'+randomUUID()+'.tmp';
 try{await writeFile(temporary,JSON.stringify(value),{flag:'wx',mode:0o600});await renameAtomic(temporary,file)}
 finally{await rm(temporary,{force:true})}
}
