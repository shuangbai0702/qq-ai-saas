import path from 'node:path';
import {readFile,writeFile,copyFile,mkdir,rename,access} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {randomUUID,createHash} from 'node:crypto';
import {build} from 'esbuild';
import {patchRuntime} from '../adapters/protocol/llbot/patch-runtime.mjs';

const args=process.argv.slice(2),value=name=>{const index=args.indexOf(name);return index<0?undefined:args[index+1]};
const archive=value('--archive'),destination=path.resolve(value('--destination')||'references/llbot-8.3.2');
if(!archive)throw Error('Usage: npm run setup:llbot -- --archive <8.3.2-test.zip> [--destination <new-directory>]');
if(process.platform!=='win32')throw Error('This reviewed archive contains Windows x64 native assets only');
try{await access(destination);throw Error('Destination already exists; use a new versioned runtime directory')}catch(error){if(error.code!=='ENOENT')throw error}
await mkdir('.local',{recursive:true});const stage=path.resolve('.local/llbot-install-'+randomUUID());
const unpack=spawnSync(process.env.LLBOT_SETUP_PYTHON||'python',['-B',path.resolve('scripts/llbot-extract.py'),path.resolve(archive),stage],{encoding:'utf8',windowsHide:true});
if(unpack.status!==0)throw Error('LLBot extraction failed; no runtime installed: '+unpack.stderr);
const runtime=path.join(stage,'bin/llbot');
await writeFile(path.join(runtime,'llbot.js'),patchRuntime(await readFile(path.join(runtime,'llbot.js'))));
await build({entryPoints:['adapters/protocol/llbot/actions.mjs'],outfile:path.join(runtime,'fishbot-actions.mjs'),bundle:true,platform:'node',target:'node22',format:'esm',logLevel:'warning'});
await copyFile('adapters/protocol/llbot/lifecycle.mjs',path.join(runtime,'fishbot-lifecycle.mjs'));
for(const file of ['llbot.js','fishbot-actions.mjs','fishbot-lifecycle.mjs']){const checked=spawnSync(process.execPath,['--check',path.join(runtime,file)],{encoding:'utf8',windowsHide:true});if(checked.status!==0)throw Error('Patched LLBot syntax check failed: '+file)}
const sha256={};
for(const file of ['bin/llbot/llbot.js','bin/llbot/fishbot-actions.mjs','bin/llbot/fishbot-lifecycle.mjs','bin/llbot/node.exe','bin/llbot/sign-proxy.win-x64.node','bin/pmhq/pmhq.exe','bin/pmhq/pmhq.dll','bin/pmhq/pmhq_loader.js',...['leaf.pem','leaf-key.pem','isrg-spki-pattern.bin','spki-inner-546.der','sign-info.json'].map(name=>'bin/pmhq/auth-local/'+name)])sha256[file]=createHash('sha256').update(await readFile(path.join(stage,file))).digest('hex');
await writeFile(path.join(stage,'fishbot-upstream.json'),JSON.stringify({version:'8.3.2-test',platform:process.platform,archiveSHA256:'cd13e20c8790fff02029153560a5467db333e10f7bec63bf77052c20cce4786c',sha256},null,2));
await mkdir(path.dirname(destination),{recursive:true});await rename(stage,destination);
console.log('LLBot private runtime installed at '+destination+'; no bundled native program was executed.');
