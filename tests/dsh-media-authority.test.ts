import test from 'node:test';
import assert from 'node:assert/strict';
import {BilibiliTools} from '../packages/database/bilibili-tools.js';

function fixture(){
 const common={bot_id:'bot',tenant_id:'tenant',work_id:'original-work',authority:'foreground',sender_id:'12345',origin_scope:'private:12345',origin_event:'original-event',generation:7,config_revision:9,settings_revision:11,account_revision:13,account_uid:'777'};
 const parent:any={...common,id:'parent',operation:'analyze',state:'finished',cancel_requested:false,completion_state:'claimed',completion_owner:'completion-owner',completion_until:new Date(Date.now()+120000).toISOString(),deadline_at:new Date(Date.now()+300000).toISOString(),result:{execution:'completed',receipt:{status:'not_applicable'}}};
 const child:any={...common,id:'child',operation:'analyze',state:'queued',cancel_requested:false,args_digest:'actual-args',deadline_at:new Date(Date.now()+300000).toISOString(),continuation:{sourceActionId:'parent',sourceOwner:'completion-owner',dsh:{delegationId:'original-delegate'}}};
 const stored=structuredClone(child),rows=new Map<string,any>([['parent',parent],['child',stored]]),reads:string[]=[];
 const repo=new BilibiliTools({query:async()=>{throw Error('Unexpected DB query: this test concerns original continuation proof only');}} as any);
 repo.get=async(tenant:string,bot:string,id:string)=>{reads.push(id);const row=rows.get(id);return row?.tenant_id===tenant&&row?.bot_id===bot?row:null;};
 return{repo,parent,child,stored,rows,reads};
}

test('admitted DSH child retains its original admission after consumed parent ACK and retirement',async()=>{
 const f=fixture();f.parent.completion_state='done';f.parent.completion_owner=null;f.parent.result=null;f.rows.delete('parent');
 await f.repo.validateContinuation(f.child);
 assert.deepEqual(f.reads,['child']);assert.equal(f.stored.state,'queued');
 assert.deepEqual(f.stored.continuation,f.child.continuation);
});

test('stored DSH child admission cannot authorize a forged identity, operation, arguments or continuation',async t=>{
 for(const field of ['work_id','sender_id','origin_scope','origin_event','generation','config_revision','settings_revision','account_revision','account_uid','args_digest','authority','bot_id','tenant_id']){
  await t.test(field,async()=>{const f=fixture();f.child[field]=typeof f.child[field]==='number'?999:'forged';await assert.rejects(f.repo.validateContinuation(f.child),/媒体任务身份不一致/);});
 }
 for(const field of ['sourceActionId','sourceOwner','dsh']){
  await t.test('continuation '+field,async()=>{const f=fixture();f.child.continuation[field]=field==='dsh'?{delegationId:'forged'}:'forged';await assert.rejects(f.repo.validateContinuation(f.child),/媒体任务身份不一致/);});
 }
 await t.test('stored operation',async()=>{const f=fixture();f.stored.operation='video_info';await assert.rejects(f.repo.validateContinuation(f.child),/媒体任务身份不一致/);});
 await t.test('stored authority',async()=>{const f=fixture();f.stored.authority='autonomous';await assert.rejects(f.repo.validateContinuation(f.child),/媒体任务身份不一致/);});
 await t.test('missing stored admission',async()=>{const f=fixture();f.rows.delete('child');await assert.rejects(f.repo.validateContinuation(f.child),/媒体任务身份不一致/);});
});

test('new media admission must still own the source live completion lease',async t=>{
 for(const mode of ['expired','foreign-owner','done','missing-parent']){
  await t.test(mode,async()=>{const f=fixture();delete f.child.id;
   if(mode==='expired')f.parent.completion_until=new Date(Date.now()-1000).toISOString();
   if(mode==='foreign-owner')f.parent.completion_owner='another-owner';
   if(mode==='done')f.parent.completion_state='done';
   if(mode==='missing-parent')f.rows.delete('parent');
   await assert.rejects(f.repo.validateContinuation(f.child),/完成租约已失效/);assert.deepEqual(f.reads,['parent']);
  });
 }
 const f=fixture();delete f.child.id;await f.repo.validateContinuation(f.child);assert.deepEqual(f.reads,['parent']);
});

test('unknown media source allows leased read continuation without changing the unknown receipt',async()=>{
 const f=fixture();delete f.child.id;f.child.operation='video_info';
 f.parent.state='unknown';f.parent.deadline_at=new Date(Date.now()-1000).toISOString();
 f.parent.result={execution:'failed',receipt:{status:'unknown'},error:{code:'executor_lost',retry:'reconcile'}};
 const original=structuredClone(f.parent);
 await f.repo.validateContinuation(f.child);
 assert.deepEqual(f.parent,original);assert.equal(f.parent.result.receipt.status,'unknown');
 f.child.operation='comment';await assert.rejects(f.repo.validateContinuation(f.child),/完成租约已失效/);
 assert.deepEqual(f.parent,original);
});


test('lost media pending ACK reuses original unfinished session child instead of replaying its aborted turn',async()=>{
 const {pendingDshMediaContinuation}=await import('../apps/node-agent/src/dsh-host.js');
 const f=fixture(),bot={id:'bot',tenant_id:'tenant',generation:7,revision:9};
 f.stored.continuation.dsh.sessionId='session';
 const repo={pending:async(_bot:any,work:string)=>{assert.equal(work,'original-work');return[{id:'parent',operation:'analyze',authority:'foreground'},f.stored];},get:f.repo.get};
 assert.equal(await pendingDshMediaContinuation(repo,bot,f.parent,'session','original-delegate','parent'),true);
 f.stored.state='finished';f.stored.completion_state='pending';
 assert.equal(await pendingDshMediaContinuation(repo,bot,f.parent,'session','original-delegate','parent'),true);
 // This also covers an ACK-lost ancestor after its immediate child has been
 // consumed: the later original child in the same session remains responsible.
 f.stored.continuation.sourceActionId='consumed-intermediate-child';
 assert.equal(await pendingDshMediaContinuation(repo,bot,f.parent,'session','original-delegate','parent'),true);
 f.stored.completion_state='done';
 assert.equal(await pendingDshMediaContinuation(repo,bot,f.parent,'session','original-delegate','parent'),false);
});

test('pending media recovery does not adopt foreign, canceled or stale original responsibilities',async t=>{
 const {pendingDshMediaContinuation}=await import('../apps/node-agent/src/dsh-host.js');
 for(const mode of ['session','delegation','work','sender','scope','event','account','settings','generation','revision','authority','operation','cancel','missing','source']){
  await t.test(mode,async()=>{
   const f=fixture(),bot={id:'bot',tenant_id:'tenant',generation:7,revision:9};f.stored.continuation.dsh.sessionId='session';
   const pending=structuredClone(f.stored),repo={pending:async()=>[pending],get:f.repo.get};
   if(mode==='session')f.stored.continuation.dsh.sessionId='foreign';
   if(mode==='delegation')f.stored.continuation.dsh.delegationId='foreign';
   if(mode==='work')f.stored.work_id='foreign';
   if(mode==='sender')f.stored.sender_id='foreign';
   if(mode==='scope')f.stored.origin_scope='group:foreign';
   if(mode==='event')f.stored.origin_event='foreign';
   if(mode==='account')f.stored.account_uid='foreign';
   if(mode==='settings')f.stored.settings_revision=999;
   if(mode==='generation')f.stored.generation=999;
   if(mode==='revision')f.stored.config_revision=999;
   if(mode==='authority')f.stored.authority='autonomous';
   if(mode==='operation')f.stored.operation='comment';
   if(mode==='cancel')f.stored.cancel_requested=true;
   if(mode==='missing')f.rows.delete('child');
   if(mode==='source')delete f.stored.continuation.sourceOwner;
   assert.equal(await pendingDshMediaContinuation(repo,bot,f.parent,'session','original-delegate','parent'),false);
  });
 }
});


test('autonomous lost ACK recovers a pending original child across cycles without reviving an old Work',async()=>{
 const {pendingDshMediaContinuation}=await import('../apps/node-agent/src/dsh-host.js'),f=fixture();
 const bot={id:'bot',tenant_id:'tenant',generation:7,revision:9},parent={...f.parent,authority:'autonomous',mode_epoch:5,work_id:'new-cycle'};
 Object.assign(f.stored,{authority:'autonomous',mode_epoch:5,work_id:'previous-resume-cycle',sender_id:null,origin_event:'previous-resume-cycle',continuation:{dsh:{sessionId:'session',delegationId:'original-delegate'}}});
 const repo={pending:async(_bot:any,work:any)=>{assert.equal(work,undefined);return[f.stored]},get:f.repo.get};
 assert.equal(await pendingDshMediaContinuation(repo,bot,parent,'session','original-delegate','parent'),true);
 f.stored.mode_epoch=6;assert.equal(await pendingDshMediaContinuation(repo,bot,parent,'session','original-delegate','parent'),false);
 f.stored.mode_epoch=5;f.stored.state='finished';f.stored.completion_state='pending';assert.equal(await pendingDshMediaContinuation(repo,bot,parent,'session','original-delegate','parent'),true);
 f.stored.completion_state='done';assert.equal(await pendingDshMediaContinuation(repo,bot,parent,'session','original-delegate','parent'),false);
});
