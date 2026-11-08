import path from 'node:path';
import {readdir,mkdir,copyFile,writeFile,readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const root=path.resolve(import.meta.dirname,'..');
process.chdir(root);
const entries=[];
async function collect(dir){for(const item of await readdir(dir,{withFileTypes:true})){const file=path.join(dir,item.name);if(item.isDirectory())await collect(file);else if(item.name.endsWith('.ts')&&!item.name.endsWith('.d.ts'))entries.push(file)}}
for(const dir of ['apps/control-api','apps/node-agent','packages','adapters'])await collect(dir);
entries.push(path.resolve('scripts/migrate-pglite-postgres.ts'));
await build({entryPoints:entries,outbase:'.',outdir:'dist-server',platform:'node',target:'node22',format:'esm',bundle:false,sourcemap:true,logLevel:'info'});
await copyFile('packages/contracts/memory-defaults.json','dist-server/packages/contracts/memory-defaults.json');
// A maintenance import must contain the changed canonical methods even when
// the running Agent has already cached their ordinary ESM modules.
await build({entryPoints:['apps/node-agent/src/maintenance.ts'],outfile:'dist-server/apps/node-agent/src/maintenance.js',platform:'node',target:'node22',format:'esm',bundle:true,packages:'external',sourcemap:true,logLevel:'info'});
await mkdir('dist-server/packages/database/migrations',{recursive:true});
for(const file of await readdir('packages/database/migrations'))if(file.endsWith('.sql'))await copyFile(`packages/database/migrations/${file}`,`dist-server/packages/database/migrations/${file}`);
const vite=spawnSync(process.execPath,['node_modules/vite/bin/vite.js','build','--config','apps/web/vite.config.ts'],{stdio:'inherit',windowsHide:true});
if(vite.status!==0)process.exit(vite.status||1);
const pkg=JSON.parse(await readFile('package.json','utf8'));
const sha=file=>createHash('sha256').update(file).digest('hex');
const sourceHashes={};for(const file of entries)sourceHashes[path.relative(root,file).split(path.sep).join('/')]=sha(await readFile(file));
// The behavior layer runs as Python source next to the compiled ESM. A
// release manifest that omits it cannot detect old source with new bundles.
const pythonSources={};const contracts={};const migrations={};
for(const file of await readdir('adapters/astrbot'))if(file.endsWith('.py'))pythonSources['adapters/astrbot/'+file]=sha(await readFile('adapters/astrbot/'+file));
for(const file of await readdir('packages/contracts'))if(file.endsWith('.json'))contracts['packages/contracts/'+file]=sha(await readFile('packages/contracts/'+file));
await mkdir('dist-server/packages/contracts',{recursive:true});
for(const name of Object.keys(contracts))await copyFile(name,path.join('dist-server',name));
for(const file of await readdir('packages/database/migrations'))if(file.endsWith('.sql'))migrations['packages/database/migrations/'+file]=sha(await readFile('packages/database/migrations/'+file));
const revision=spawnSync('git',['rev-parse','HEAD'],{encoding:'utf8',windowsHide:true});
if(revision.status!==0)throw new Error('Production build requires a traceable Git revision');
const tree=spawnSync('git',['status','--porcelain','--untracked-files=normal'],{encoding:'utf8',windowsHide:true});
if(tree.status!==0)throw new Error('Cannot verify production source state');
await writeFile('dist-server/build.json',JSON.stringify({builtAt:new Date().toISOString(),gitCommit:revision.stdout.trim(),gitDirty:!!tree.stdout.trim(),node:process.version,version:pkg.version,entry:'apps/control-api/src/main.js',sourceHashes,pythonSources,contracts,migrations,note:'Compiled ESM: no tsx or resident Vite. Keep source Python launchers and references alongside this output.'},null,2));
