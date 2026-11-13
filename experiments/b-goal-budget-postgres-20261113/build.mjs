import {build} from 'esbuild';
import {mkdir,readdir,copyFile,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const sourceDir=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(sourceDir,'../..');
// Generated bundle/migrations stay outside the versioned experiment directory.
const out=path.resolve(root,process.env.FISHBOT_PG_PROBE_OUTPUT||'.local/pg-budget-probe-repro');
if(!out.startsWith(root+path.sep)||out===sourceDir)throw new Error('Output must be a task-local directory inside the repository');
await mkdir(out,{recursive:true});
await build({absWorkingDir:root,entryPoints:[path.join(sourceDir,'probe.ts')],outfile:path.join(out,'probe.mjs'),platform:'node',target:'node22',format:'esm',bundle:true,packages:'external',logLevel:'info'});
await mkdir(path.join(out,'migrations'),{recursive:true});
const files=['probe.mjs'];
for(const name of await readdir(path.join(root,'packages/database/migrations')))if(name.endsWith('.sql')){
 await copyFile(path.join(root,'packages/database/migrations',name),path.join(out,'migrations',name));files.push('migrations/'+name);
}
const sha=async file=>createHash('sha256').update(await readFile(file)).digest('hex');
const artifactHashes={};for(const name of files)artifactHashes[name]=await sha(path.join(out,name));
const sourceHashes={};
for(const name of [path.relative(root,path.join(sourceDir,'probe.ts')),'packages/database/model-request-budget.ts','packages/database/autonomous.ts','packages/database/tool-effects.ts','packages/database/index.ts'])sourceHashes[name.replaceAll('\\','/')]=await sha(path.join(root,name));
await writeFile(path.join(out,'manifest.json'),JSON.stringify({kind:'isolated-postgres-budget-probe',createdAt:new Date().toISOString(),sourceHashes,artifactHashes},null,2));
console.log(JSON.stringify({prepared:true,scenarios:7,out: path.relative(root,out),bundleSha256:artifactHashes['probe.mjs']}));
