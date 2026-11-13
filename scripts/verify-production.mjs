import path from 'node:path';
import {readFile,access} from 'node:fs/promises';
import {createHash} from 'node:crypto';
export async function verifyProduction(root){
 const manifest=JSON.parse(await readFile(path.join(root,'dist-server/build.json'),'utf8'));
 if(manifest.gitDirty)throw new Error('Production build has uncommitted inputs');
 if(!manifest.artifactHashes)throw new Error('Production artifact fingerprint is missing; rebuild first');
 let sources={};try{await access(path.join(root,'packages/database/index.ts'));sources=manifest.sourceHashes}catch(error){if(error.code!=='ENOENT')throw error}
 for(const [file,expected] of Object.entries({...sources,...manifest.pythonSources,...manifest.runtimeScripts,...manifest.contracts,...manifest.artifactHashes})){
  const target=path.resolve(root,file);
  if(!target.startsWith(path.resolve(root)+path.sep))throw new Error('Invalid production fingerprint path');
  const actual=createHash('sha256').update(await readFile(target)).digest('hex');
  if(actual!==expected)throw new Error('Production input/artifact drift: '+file);
 }
 return manifest;
}
