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
await mkdir('dist-server/packages/database/migrations',{recursive:true});
for(const file of await readdir('packages/database/migrations'))if(file.endsWith('.sql'))await copyFile(`packages/database/migrations/${file}`,`dist-server/packages/database/migrations/${file}`);
const vite=spawnSync(process.execPath,['node_modules/vite/bin/vite.js','build','--config','apps/web/vite.config.ts'],{stdio:'inherit',windowsHide:true});
if(vite.status!==0)process.exit(vite.status||1);
const pkg=JSON.parse(await readFile('package.json','utf8'));
const sourceHashes={};for(const file of entries)sourceHashes[file]=createHash('sha256').update(await readFile(file)).digest('hex');
await writeFile('dist-server/build.json',JSON.stringify({builtAt:new Date().toISOString(),node:process.version,version:pkg.version,entry:'apps/control-api/src/main.js',sourceHashes,note:'Compiled ESM: no tsx or resident Vite. Keep source Python launchers and references alongside this output.'},null,2));
