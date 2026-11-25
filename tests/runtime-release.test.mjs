import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {checkRuntimeRelease,applyRuntimeRelease,releaseFiles} from '../scripts/runtime-release.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
async function fixture(t){
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'fishbot-release-test-'));
 t.after(()=>fs.rm(dir,{recursive:true,force:true}));
 const root=path.join(dir,'root'),stage=path.join(dir,'stage');
 const write=async(file,value)=>{await fs.mkdir(path.dirname(file),{recursive:true});await fs.writeFile(file,typeof value==='string'?value:JSON.stringify(value))};
 const current={gitCommit:'a'.repeat(40),gitDirty:false,artifactHashes:{}};
 const next={...current,gitCommit:'b'.repeat(40)};
 await write(path.join(root,'dist-server/build.json'),current);
 await write(path.join(root,'package.json'),{type:'module',scripts:{},dependencies:{ws:'1'}});
 const spec={commit:next.gitCommit,oldManifestSha256:sha(JSON.stringify(current)),files:{},verifyFiles:{},retired:[]},expected={};
 const add=async(name,value)=>{
  const raw=typeof value==='string'?value:JSON.stringify(value);
  await write(path.join(stage,'staged',name),raw);spec.files[name]=spec.verifyFiles[name]=sha(raw);
  expected[name]=await fs.readFile(path.join(root,name)).then(sha,e=>{if(e.code==='ENOENT')return null;throw e});
 };
 await add('adapters/astrbot/memory_review.py','new review');await add('dist-server/build.json',next);
 const save=async()=>{await write(path.join(stage,'release.json'),spec);await write(path.join(stage,'expected-before.json'),expected)};
 await save();return{root,stage,current,next,spec,expected,add,save,write,profile:{sourceRoot:root}};
}

test('runtime preflight accepts exact source changes and script-only package changes',async t=>{
 const f=await fixture(t);
 await f.add('package.json',{type:'module',scripts:{'deploy:runtime':'node scripts/runtime-release.mjs'},dependencies:{ws:'1'}});await f.save();
 assert.equal((await checkRuntimeRelease(f.profile,f.stage)).alreadyApplied,false);
 await f.add('package.json',{type:'module',dependencies:{ws:'2'}});await f.save();
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/requires installation/);
});

test('a tampered staged file or concurrent production change cannot activate',async t=>{
 const f=await fixture(t);
 await f.write(path.join(f.stage,'staged/adapters/astrbot/memory_review.py'),'tampered');
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/Staged hash mismatch/);
 await f.add('adapters/astrbot/memory_review.py','correct');await f.save();
 await f.write(path.join(f.root,'dist-server/build.json'),{...f.current,builtAt:'concurrent'});
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/Concurrent release manifest change/);
});

test('an unrelated component build cannot use the shared Worker release path',async t=>{
 const f=await fixture(t);
 f.next.artifactHashes={'dist-server/apps/control-api/src/app.js':'c'.repeat(64)};
 await f.add('dist-server/build.json',f.next);await f.save();
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/different component release/);
});

test('missing file CAS, incomplete inventory and escaped paths fail before installation',async t=>{
 const f=await fixture(t),name='adapters/astrbot/memory_review.py';
 delete f.expected[name];await f.save();
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/Missing activation CAS/);
 f.expected[name]=null;delete f.spec.verifyFiles[name];await f.save();
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/Incomplete release inventory/);
 f.expected['../outside']=null;await f.save();
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/Invalid release path/);
});

test('having the new disk commit does not fabricate runtime application success',async t=>{
 const f=await fixture(t);await f.write(path.join(f.root,'dist-server/build.json'),f.next);
 assert.equal((await checkRuntimeRelease(f.profile,f.stage)).alreadyApplied,true);
 await assert.rejects(applyRuntimeRelease({},f.profile,f.stage),{code:'ENOENT'});
 await f.write(path.join(f.stage,'result.json'),{ok:false});
 await assert.rejects(applyRuntimeRelease({},f.profile,f.stage),/runtime verification/);
});

test('all verification paths are checked before installation, including inherited build entries',async t=>{
 const f=await fixture(t),name='dist-server/.local/old-export/scripts/migrate-pglite-postgres.js',raw='old compiled migration';
 await f.write(path.join(f.root,name),raw);f.current.artifactHashes[name]=sha(raw);
 await f.write(path.join(f.root,'dist-server/build.json'),f.current);f.spec.oldManifestSha256=sha(JSON.stringify(f.current));
 f.spec.verifyFiles[name]=sha(raw);await f.add('dist-server/build.json',f.next);await f.save();
 assert.equal((await checkRuntimeRelease(f.profile,f.stage)).alreadyApplied,false);
 f.spec.verifyFiles['.local/private.json']='c'.repeat(64);await f.save();
 await assert.rejects(checkRuntimeRelease(f.profile,f.stage),/Invalid release path/);
});

test('the host installer owns code mutation, rollback is exact and preserves third-party changes',async t=>{
 const f=await fixture(t),files=releaseFiles(await checkRuntimeRelease(f.profile,f.stage),f.stage);
 await files.install();
 assert.equal(JSON.parse(await fs.readFile(path.join(f.root,'dist-server/build.json'))).gitCommit,f.next.gitCommit);
 await f.write(path.join(f.root,'adapters/astrbot/memory_review.py'),'another release');
 await assert.rejects(files.rollback(),/Concurrent file change/);
 assert.equal(await fs.readFile(path.join(f.root,'adapters/astrbot/memory_review.py'),'utf8'),'another release');
 await f.write(path.join(f.root,'adapters/astrbot/memory_review.py'),'new review');
 await files.rollback();await files.rollback();
 assert.equal(JSON.parse(await fs.readFile(path.join(f.root,'dist-server/build.json'))).gitCommit,f.current.gitCommit);
 await assert.rejects(fs.access(path.join(f.root,'adapters/astrbot/memory_review.py')),{code:'ENOENT'});
});
