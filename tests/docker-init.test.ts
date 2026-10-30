import test from 'node:test';import assert from 'node:assert/strict';import {execFile} from 'node:child_process';import {mkdtemp,readFile,writeFile} from 'node:fs/promises';import path from 'node:path';
const invoke=(root:string)=>new Promise<{output:string;error:Error|null}>((resolve)=>execFile(process.execPath,['scripts/docker-init.mjs',root,'admin@deploy.test','https://bots.example.test'],{windowsHide:true},(error,out,err)=>resolve({output:out+err,error})));
test('Docker setup creates separate secrets, never prints them and preserves both files on a repeated launch',async()=>{
 const root=await mkdtemp(path.resolve('.local/docker-init-'));const result=await invoke(root);assert.equal(result.error,null);const secret=await readFile(path.join(root,'.local/docker-secrets/admin-password'),'utf8'),env=await readFile(path.join(root,'.env.docker'),'utf8');assert.equal(secret.trim().length,32);assert.ok(!result.output.includes(secret.trim()));assert.ok(!env.includes(secret.trim()));assert.match(env,/COOKIE_SECURE=true/);assert.match(env,/BIND_ADDRESS=127.0.0.1/);
 assert.equal((await invoke(root)).error,null);assert.equal(await readFile(path.join(root,'.local/docker-secrets/admin-password'),'utf8'),secret);assert.equal(await readFile(path.join(root,'.env.docker'),'utf8'),env);
});
test('Docker setup fails closed on a partial existing deployment instead of replacing credentials',async()=>{
 const root=await mkdtemp(path.resolve('.local/docker-init-partial-')),env=path.join(root,'.env.docker');await writeFile(env,'previous deployment config\n');const result=await invoke(root);assert.ok(result.error);assert.match(result.output,/Incomplete deployment configuration/);assert.equal(await readFile(env,'utf8'),'previous deployment config\n');
});
test('pinned NapCat build extraction rejects archive traversal and symbolic links',async()=>{
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);const output=await new Promise<string>((resolve,reject)=>execFile(python,['experiments/docker-audit-20261029/napcat_archive_test.py'],{windowsHide:true},(error,out,err)=>error?reject(new Error(err)):resolve(out)));assert.match(output,/PASS pinned shell extraction/);
});
for(const mode of ['container','unlabelled-volume'])test(`fresh Docker deployment refuses an existing ${mode} before initialization`,async()=>{
 const root=await mkdtemp(path.resolve('.local/docker-up-guard-'));const {mkdir,copyFile}=await import('node:fs/promises');await mkdir(path.join(root,'docker'));
 const script=path.join(root,'docker',process.platform==='win32'?'up.ps1':'up.sh');await copyFile(path.resolve('docker',path.basename(script)),script);
 let result:string;
 if(process.platform==='win32'){
  const driver=path.join(root,'driver.ps1');await writeFile(driver,`param([string]$UpScript,[string]$Mode)
$ErrorActionPreference='Stop'
$env:COMPOSE_PROJECT_NAME='fishbot-saas'
function global:docker {
  $global:LASTEXITCODE=0
  if($args[0] -eq 'compose' -and $args[1] -eq 'version'){return}
  if($args[0] -eq 'ps'){if($Mode -eq 'container'){'fixture-existing-container'};return}
  if($args[0] -eq 'volume' -and $args[1] -eq 'ls'){if($Mode -eq 'unlabelled-volume' -and $args -contains '--format'){'fishbot-saas_fishbot-data'};return}
  throw 'Unexpected Docker command: mutation must not be reached'
}
$taskCaught=$false
try {& $UpScript -Email admin@guard.test -Origin http://127.0.0.1:8787} catch {
  if($_.Exception.Message -notmatch 'Existing deployment or data volume found'){throw}
  $taskCaught=$true
}
if(-not $taskCaught){throw 'Fresh setup accepted an existing project'}
Write-Output 'PASS existing deployment guard'
`);
  result=await new Promise<string>((resolve,reject)=>execFile('pwsh',['-NoProfile','-File',driver,'-UpScript',script,'-Mode',mode],{windowsHide:true},(error,out,err)=>error?reject(new Error(out+err)):resolve(out)));
 }else{
  const bin=path.join(root,'bin');await mkdir(bin);await writeFile(path.join(bin,'docker'),`#!/bin/sh
case "$1:$2" in
 compose:version|image:inspect) exit 0;;
 ps:*) [ "$FISHBOT_GUARD_FIXTURE" != container ] || printf '%s\\n' existing-container; exit 0;;
 volume:ls) if [ "$FISHBOT_GUARD_FIXTURE" = unlabelled-volume ] && [ "$3" = -q ]; then exit 0; fi; [ "$FISHBOT_GUARD_FIXTURE" != unlabelled-volume ] || printf '%s\\n' fishbot-saas_fishbot-data; exit 0;;
 volume:inspect) [ "$FISHBOT_GUARD_FIXTURE" = unlabelled-volume ]; exit $?;;
 *) printf '%s\\n' 'Unexpected Docker mutation' >&2; exit 99;;
esac
`,{mode:0o700});result=await new Promise<string>((resolve,reject)=>execFile('/bin/sh',[script],{env:{...process.env,COMPOSE_PROJECT_NAME:'fishbot-saas',FISHBOT_GUARD_FIXTURE:mode,PATH:bin+path.delimiter+process.env.PATH}},(error,out,err)=>error?.code===1?resolve(out+err):reject(new Error(out+err))));
 }
 assert.match(result,process.platform==='win32'?/PASS existing deployment guard/:/Existing deployment or data volume found/);
 const {access}=await import('node:fs/promises');await assert.rejects(access(path.join(root,'.env.docker')),{code:'ENOENT'});await assert.rejects(access(path.join(root,'.local')),{code:'ENOENT'});
});
