import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {access,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
const root=path.resolve(import.meta.dirname,'..');process.chdir(root);
process.env.NODE_ENV='production';
await import('dotenv/config');
const entry=path.join(root,'dist-server/apps/control-api/src/main.js');
try{await access(entry);await access(path.join(root,'apps/web/dist/index.html'))}catch{throw new Error('Production assets missing. First run node scripts/production-build.mjs')}
await import(pathToFileURL(entry).href);
const dataRoot=path.resolve(process.env.DATA_ROOT||'.local');await mkdir(dataRoot,{recursive:true});
await writeFile(path.join(dataRoot,'production-process.json'),JSON.stringify({api:process.pid,startedAt:new Date().toISOString(),mode:'compiled-single-node',execArgv:process.execArgv}));
// Local filesystem request, not an unauthenticated HTTP shutdown endpoint.
const stopFile=path.join(dataRoot,'production.stop');let closing=false;
setInterval(async()=>{if(closing)return;try{if(Number(await readFile(stopFile,'utf8'))===process.pid){closing=true;await rm(stopFile);process.emit('SIGTERM')}}catch{}},500).unref();
