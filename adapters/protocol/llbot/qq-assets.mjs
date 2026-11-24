import path from 'node:path';
import {mkdir,readdir,readFile,writeFile,copyFile,link,symlink,realpath,access} from 'node:fs/promises';

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
 try{if(JSON.stringify(JSON.parse(await readFile(markerFile,'utf8')))!==JSON.stringify(marker))throw Error('QQNT private asset ownership changed');return path.join(root,'QQ.exe')}catch(e){if(e.code!=='ENOENT')throw e}
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
   }else if(name===path.join('versions','config.json'))await copyFile(src,dest);
   else if(name===path.join(app,'package.json'))await writeFile(dest,JSON.stringify({...metadata,main:'./fishbot-entry.cjs'}));
   else{
    try{await link(src,dest)}catch(e){if(e.code==='EXDEV')await copyFile(src,dest);else throw e}
   }
  }
 }
 await copy();
 await writeFile(path.join(root,app,'fishbot-entry.cjs'),`require(${JSON.stringify(bootstrap)});\nrequire(${JSON.stringify('./'+main.replace(/^\.\//,''))});\n`,{mode:0o600});
 await writeFile(markerFile,JSON.stringify(marker),{mode:0o600});
 return path.join(root,'QQ.exe');
}
