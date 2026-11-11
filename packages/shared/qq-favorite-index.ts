/** One index authority, shared by HTTP edits and Agent maintenance. */
import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import lockfile from 'proper-lockfile';
export const favoriteDirectory=(root:string,id:string)=>path.join(root,'bots',id,'qq-favorites');
export async function withFavoriteIndex<T>(root:string,id:string,run:()=>Promise<T>){
 const directory=favoriteDirectory(root,id);await mkdir(directory,{recursive:true});
 const release=await lockfile.lock(directory,{realpath:false,stale:30000,update:10000,retries:{retries:12,minTimeout:25,maxTimeout:250}});
 try{return await run()}finally{await release()}
}
export async function readFavoriteIndex(root:string,id:string):Promise<any>{
 try{return JSON.parse(await readFile(path.join(favoriteDirectory(root,id),'index.json'),'utf8'))}catch(e:any){if(e.code==='ENOENT')return{account:'',enabled:false,items:[]};throw e}
}
export async function writeFavoriteIndex(root:string,id:string,value:unknown){
 const directory=favoriteDirectory(root,id),target=path.join(directory,'index.json'),temp=target+'.'+randomUUID()+'.tmp';
 await mkdir(directory,{recursive:true});
 try{await writeFile(temp,JSON.stringify(value),{flag:'wx',mode:0o600});for(let attempt=0;;attempt++){try{await rename(temp,target);break}catch(e:any){if(process.platform!=='win32'||!['EPERM','EACCES','EBUSY'].includes(e.code)||attempt>=4)throw e;await new Promise(r=>setTimeout(r,25*(attempt+1)))}}}finally{await rm(temp,{force:true})}
}
