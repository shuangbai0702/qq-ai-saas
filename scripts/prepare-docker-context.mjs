import {spawnSync} from 'node:child_process';
import path from 'node:path';import {mkdir,lstat,readdir,copyFile,writeFile,readFile} from 'node:fs/promises';import {createHash} from 'node:crypto';
const root=path.resolve(import.meta.dirname,'..'),destination=path.resolve(root,process.argv[2]||'.local/docker-context');
if(!destination.startsWith(path.join(root,'.local')+path.sep))throw new Error('Build context must be a new directory under this project .local');
try{await lstat(destination);throw new Error('Context already exists; choose a new .local/docker-context-VERSION directory')}catch(e){if(e.code!=='ENOENT')throw e}
await mkdir(destination,{recursive:true});const files=[];
const loader=JSON.parse(await readFile(path.join(root,'docker/napcat-upstream.json'),'utf8'));
const loaderPath='references/napcat-linux-loader/node_addon.node';
let loaderBytes;try{loaderBytes=await readFile(path.join(root,loaderPath))}catch(e){if(e.code!=='ENOENT')throw e;const response=await fetch(`https://raw.githubusercontent.com/NapNeko/NapCatLinuxNodeLoader/${loader.commit}/${loader.loaderPath}`,{signal:AbortSignal.timeout(30000)});if(!response.ok)throw new Error('Pinned Linux QQ loader download failed: '+response.status);loaderBytes=Buffer.from(await response.arrayBuffer());if(loaderBytes.length>1024*1024)throw new Error('Unexpected loader size');await mkdir(path.dirname(path.join(root,loaderPath)),{recursive:true});await writeFile(path.join(root,loaderPath),loaderBytes,{flag:'wx'})}
if(createHash('sha256').update(loaderBytes).digest('hex')!==loader.loaderSha256)throw new Error('Pinned Linux QQ loader checksum mismatch');
async function copy(source,target){const info=await lstat(path.join(root,source));if(info.isSymbolicLink())throw new Error('Symlinks are forbidden in release context: '+source);if(info.isDirectory()){for(const entry of await readdir(path.join(root,source)))if(!['node_modules','__pycache__','.git','.venv','dist','design-exploration'].includes(entry))await copy(path.join(source,entry),path.join(target,entry));return}if(!info.isFile()||/(^|[\\/])(?:\.env[^\\/]*|master\.key|.*\.process\.json)$|\.pyc$/i.test(source))throw new Error('Unexpected release file: '+source);const to=path.join(destination,target);await mkdir(path.dirname(to),{recursive:true});await copyFile(path.join(root,source),to);files.push({path:target.split(path.sep).join('/'),sha256:createHash('sha256').update(await readFile(to)).digest('hex')})}
for(const name of ['Dockerfile','.dockerignore','package.json','package-lock.json','tsconfig.json','apps/control-api/src','apps/node-agent/src','apps/web/src','apps/web/index.html','apps/web/vite.config.ts','apps/web/public/illustrations','packages','adapters','templates','docker'])await copy(name,name);
// Runtime/build entry points only: historical QQ experiments and local operator
// scripts do not belong in a distributable image.
for(const name of ['production-build.mjs','production-start.mjs','verify-production.mjs','docker-migrate.mjs','process_control.py','runtime_memory.py','docker-health.mjs','docker-agent-health.mjs','docker-entrypoint.mjs','docker-init.mjs','bootstrap-admin.mjs','setup-linux-napcat.py','napcat_login_patch.py','migrate-pglite-postgres.ts'])await copy('scripts/'+name,'scripts/'+name);
for(const name of ['astrbot','runtime_bootstrap.py','pyproject.toml','uv.lock','README.md','LICENSE'])await copy('references/astrbot/'+name,'upstream/astrbot/'+name);
await copy(loaderPath,'upstream/napcat-loader/node_addon.node');
const template=JSON.parse(await readFile(path.join(root,'resources/pglite-template/manifest.json'),'utf8'));if(template.empty!==true)throw new Error('Only a verified EMPTY PGlite template may enter an image');await copy('resources/pglite-template','resources/pglite-template');
await copy('references/astrbot-version.json','upstream/astrbot-version.json');
const upstream=JSON.parse(await readFile(path.join(destination,'upstream/astrbot-version.json'),'utf8'));if(!/^[a-f0-9]{40}$/.test(upstream.commit))throw new Error('Missing pinned AstrBot commit');
await writeFile(path.join(destination,'context-manifest.json'),JSON.stringify({createdAt:new Date().toISOString(),upstream,files},null,2));console.log(JSON.stringify({context:destination,files:files.length,upstream:upstream.commit,secretsIncluded:false}));

const revision=spawnSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8',windowsHide:true});
const status=spawnSync('git',['status','--porcelain','--untracked-files=normal'],{cwd:root,encoding:'utf8',windowsHide:true});
if(revision.status!==0||status.status!==0)throw new Error('Release export requires a traceable Git checkout');
await writeFile(path.join(destination,'release-source.json'),JSON.stringify({version:1,gitCommit:revision.stdout.trim(),gitDirty:!!status.stdout.trim(),files:Object.fromEntries(files.map(file=>[file.path,file.sha256]))}));
