import test from 'node:test';import assert from 'node:assert/strict';
import {deliveryWarning,runtimeStatus} from '../packages/shared/runtime-status.js';
import {ProcessHost} from '../apps/node-agent/src/process-host.js';
test('QQ online and last uncertain delivery remain distinct; a receipt clears warning',()=>{
 const now=Date.now(),bot:any={desired_state:'running',health:{sampledAt:new Date(now).toISOString(),protocol:{online:true},ai:{metrics:{lastDelivery:{at:now/1000,status:'unknown',reason:'qq_send_unconfirmed'}}}}};
 assert.equal(runtimeStatus(bot,now).online,true);assert.equal(deliveryWarning(bot,now),'QQ 上次发送未确认');
 bot.health.ai.metrics.lastDelivery={at:now/1000,status:'confirmed'};assert.equal(deliveryWarning(bot,now),null);
 bot.health.ai.metrics.lastDelivery={at:now/1000,status:'rejected',reason:'qq_write_restricted'};assert.equal(deliveryWarning(bot,now),'QQ 上次发送被限制');
 assert.equal(deliveryWarning(bot,now+91000),null);bot.health.protocol.online=false;assert.equal(deliveryWarning(bot,now),null);
 bot.health.protocol.online=true;bot.health.ai.metrics.lastDelivery.at=now/1000+60;assert.equal(deliveryWarning(bot,now),null);
});
test('one inaccessible process neither aborts peer inspection nor permits launch',async()=>{
 const host:any=new ProcessHost('fixture','unused');host.record=async(id:string,kind:string)=>kind==='protocol'?{pid:id==='denied'?1:2,created:1}:null;
 // Inspection itself is tested directly as well as through the actual batch
 // decoder, without needing a genuinely protected Windows process.
 host.helper=async(args:string[])=>JSON.stringify(args[0]==='batch'?[{id:'denied',kind:'protocol',valid:false,inspectionError:'access_denied'},{id:'healthy',kind:'protocol',valid:true,pid:2}]:{valid:false,inspectionError:'access_denied'});
 await assert.rejects(host.inspect('denied','protocol'),/无法核实进程归属/);
 await assert.rejects(host.start('denied','protocol','unused',[],'.',{}),/无法核实进程归属/);
 await host.withInspection(async()=>{
  // Feed the batch returned by the real helper through beginInspection.
  host.maintainMemory=async()=>{};await host.beginInspection();
  await assert.rejects(host.inspect('denied','protocol'),/无法核实进程归属/);
  assert.equal((await host.inspect('healthy','protocol')).pid,2);
 });
});
