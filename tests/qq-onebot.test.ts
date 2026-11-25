import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {nativeCall} from '../packages/shared/qq-onebot.js';
import {NapCatAdapter} from '../adapters/protocol/napcat/index.js';
import {LLBotAdapter} from '../adapters/protocol/llbot/index.js';
import {QQ_ONEBOT_CAPABILITIES} from '../packages/contracts/index.js';

test('both QQ backends share business capabilities and native dispatch without write retries or cross-backend fallback',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'qq-onebot-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const calls:any[]=[];let fail=false;
 const server=createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;calls.push({url:req.url,auth:req.headers.authorization,params:JSON.parse(raw)});res.setHeader('Content-Type','application/json');res.end(JSON.stringify(fail?{status:'failed',retcode:1504}:{status:'ok',retcode:0,data:{receipt:'fixture'}}))});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise<void>(r=>server.close(()=>r())));const port=(server.address() as any).port;
 for(const protocol of ['napcat','llbot']){
  const bot={id:protocol,protocol},folder=path.join(root,'bots',bot.id);await mkdir(folder,{recursive:true});
  const manifest={botId:bot.id,management:{host:'127.0.0.1',port:port-(protocol==='napcat'?1:0),token:'management-'+protocol},reverseWs:{token:'onebot-'+protocol}};
  const file=path.join(folder,protocol+'-platform.json');await writeFile(file,JSON.stringify(manifest));
  fail=false;
  for(const action of ['get_login_info','get_group_member_info','send_group_msg','set_qq_profile','add_custom_face','send_qzone_msg']){
   const params={fixture:action};assert.deepEqual(await nativeCall(root,bot,action,params,new AbortController().signal),{receipt:'fixture'});
   assert.deepEqual(calls.at(-1),{url:'/'+(protocol==='llbot'?'actions/':'')+action,auth:'Bearer '+(protocol==='llbot'?manifest.management.token:manifest.reverseWs.token),params});
  }
  fail=true;const before=calls.length;await assert.rejects(nativeCall(root,bot,'send_qzone_msg',{},new AbortController().signal),/不要自动重发/);assert.equal(calls.length,before+1);
  manifest.botId='other';await writeFile(file,JSON.stringify(manifest));await assert.rejects(nativeCall(root,bot,'get_login_info',{},new AbortController().signal),/尚未就绪/);assert.equal(calls.length,before+1);
 }
 const host:any={};for(const adapter of [new NapCatAdapter(host,''),new LLBotAdapter(host,'')])for(const capability of QQ_ONEBOT_CAPABILITIES)assert.ok(adapter.getCapabilities().includes(capability));
 await assert.rejects(nativeCall(root,{id:'no',protocol:'mock'},'get_login_info',{},new AbortController().signal),/尚不支持/);
});
