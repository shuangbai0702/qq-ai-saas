import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {syncBuiltinESMExports} from 'node:module';
import {createHash} from 'node:crypto';
import path from 'node:path';
import Fastify from 'fastify';
import {registerSocialMedia} from '../apps/control-api/src/social-media.js';

for(const preexisting of [true,false])test(`failed upload preserves borrowed image and releases owned image: preexisting=${preexisting}`,async t=>{
 const root=await fs.mkdtemp(path.resolve('.local/social-rollback-')),app=Fastify();
 const bytes=await fs.readFile('templates/reactions/laugh.png'),id='usr_'+createHash('sha256').update(bytes).digest('hex').slice(0,16);
 const dir=path.join(root,'bots/a/reactions'),image=path.join(dir,id+'.png');
 await fs.mkdir(dir,{recursive:true});if(preexisting)await fs.writeFile(image,bytes);
 registerSocialMedia(app,root,async()=>({id:'a'}));
 const rename=fs.rename;
 t.mock.method(fs,'rename',async(from:Parameters<typeof rename>[0],to:Parameters<typeof rename>[1])=>{if(String(to)===path.join(dir,id+'.json'))throw Object.assign(new Error('fixture metadata commit failure'),{code:'EIO'});return rename(from,to)});
 syncBuiltinESMExports();
 try{
  const response=await app.inject({method:'POST',url:'/v1/bots/a/reactions',payload:{png:bytes.toString('base64'),meaning:'开心时使用'}});
  assert.equal(response.statusCode,500);
  if(preexisting)assert.deepEqual(await fs.readFile(image),bytes);
  else await assert.rejects(fs.readFile(image),{code:'ENOENT'});
  assert.deepEqual(await fs.readdir(dir),preexisting?[id+'.png']:[]);
 }finally{t.mock.restoreAll();syncBuiltinESMExports();await app.close();await fs.rm(root,{recursive:true,force:true})}
});
