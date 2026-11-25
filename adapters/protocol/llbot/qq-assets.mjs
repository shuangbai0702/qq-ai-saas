import path from 'node:path';
import {mkdir,readdir,readFile,writeFile,copyFile,link,symlink,realpath,access,rename,rm} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';

const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
// QQ's Windows shell validates package.json before Electron enters main.
// The upstream LiteLoaderQQNT/QQNTFileVerifyPatch x64 signature identifies
// the verification call; change only that call in the Bot's private DLL.
export function privateQqShell(source){
 const bytes=Buffer.from(source),pe=bytes.length>=64?bytes.readUInt32LE(60):-1;
 if(bytes.toString('ascii',0,2)!=='MZ'||pe<64||pe+6>bytes.length||bytes.toString('ascii',pe,pe+4)!=='PE\0\0'||bytes.readUInt16LE(pe+4)!==0x8664)throw Error('Unsupported QQNT Windows x64 shell');
 const signature=Buffer.from('4889ce488b114c8b41084929d0488b4918e8','hex'),offset=bytes.indexOf(signature);
 if(offset<0||offset+22>bytes.length||bytes.indexOf(signature,offset+1)>=0)throw Error('QQNT private entry verification signature is not unique');
 bytes.set([0xb8,1,0,0,0],offset+17);
 return bytes;
}

/** Private writable launcher metadata; shared native assets contain no account state. */
export async function prepareQqAssets(sourceExe,home,bootstrap){
 const source=path.dirname(await realpath(sourceExe)),root=path.join(home,'program');
 const versions=JSON.parse(await readFile(path.join(source,'versions/config.json'),'utf8'));
 const version=versions.curVersion;
 if(typeof version!=='string'||!/^[\d.]+-\d+$/.test(version))throw Error('Unsupported QQNT version layout');
 const app=path.join('versions',version,'resources','app');
 const metadata=JSON.parse(await readFile(path.join(source,app,'package.json'),'utf8'));
 const main=metadata.main;
 if(typeof main!=='string'||path.isAbsolute(main)||main.split(/[\\/]/).includes('..'))throw Error('Unsupported QQNT main entry');
 const entry=main.split(/[\\/]/).filter(part=>part!=='.'),archive=entry.findIndex(part=>part.endsWith('.asar'));
 // Ordinary Node cannot traverse Electron's virtual ASAR filesystem.
 await access(path.join(source,app,...entry.slice(0,archive<0?entry.length:archive+1)));
 const marker={source,version,main,bootstrap};
 const markerFile=path.join(root,'fishbot-assets.json');
 const shell=path.join('versions',version,'QQNT.dll'),originalShell=await readFile(path.join(source,shell)),patchedShell=privateQqShell(originalShell);
 const entryFile=path.join(root,app,'fishbot-entry.cjs'),entryCode=`require(${JSON.stringify(bootstrap)});\nrequire(${JSON.stringify('./'+main.replace(/^\.\//,''))});\n`;
 async function replace(file,bytes){const temporary=file+'.'+randomUUID();try{await writeFile(temporary,bytes,{flag:'wx',mode:0o600});await rename(temporary,file)}finally{await rm(temporary,{force:true})}}
 try{
  if(JSON.stringify(JSON.parse(await readFile(markerFile,'utf8')))!==JSON.stringify(marker))throw Error('QQNT private asset ownership changed');
  const file=path.join(root,shell),current=hash(await readFile(file));
  if(current!==hash(patchedShell)){if(current!==hash(originalShell))throw Error('QQNT private shell ownership changed');await replace(file,patchedShell)}
  if(await readFile(entryFile,'utf8')!==entryCode)await replace(entryFile,entryCode);
  return path.join(root,'QQ.exe');
 }catch(e){if(e.code!=='ENOENT')throw e}
 await mkdir(root);
 const branches=new Set(['versions',path.join('versions',version),path.join('versions',version,'resources'),app]);
 async function copy(relative=''){
  const destination=path.join(root,relative);await mkdir(destination,{recursive:true});
  for(const item of await readdir(path.join(source,relative),{withFileTypes:true})){
   const name=path.join(relative,item.name),src=path.join(source,name),dest=path.join(root,name);
   if(item.isSymbolicLink())throw Error('QQNT source assets must be ordinary files/directories');
   if(item.isDirectory()){
    if(branches.has(name))await copy(name);
    else{try{if(await realpath(dest)!==await realpath(src))throw Error('QQNT asset directory ownership mismatch')}catch(e){if(e.code!=='ENOENT')throw e;await symlink(src,dest,process.platform==='win32'?'junction':'dir')}}
   }else if(name===shell)await writeFile(dest,patchedShell,{flag:'wx',mode:0o600});
   else if(name===path.join('versions','config.json'))await copyFile(src,dest);
   else if(name===path.join(app,'package.json'))await writeFile(dest,JSON.stringify({...metadata,main:'./fishbot-entry.cjs'}));
   else{
    try{await link(src,dest)}catch(e){if(e.code==='EXDEV')await copyFile(src,dest);else throw e}
   }
  }
 }
 await copy();
 await writeFile(entryFile,entryCode,{mode:0o600});
 await writeFile(markerFile,JSON.stringify(marker),{mode:0o600});
 return path.join(root,'QQ.exe');
}
