"""NULL-round Hold repairs keep the original batch and earned permit identity."""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from summary_material_test import message,SCOPE
import summary_material as material
import message_provenance

class NullHoldRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.r=self.store.reflection
        self.var=contextvars.ContextVar('null_hold',default=None);self.calls=[];self.metrics={}
        self.scope='bot_shared';self.event=json.dumps([SCOPE,'silent'],separators=(',',':'))
        self.native='其他Work的原稿';self.fail=False;self.barrier=None
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def chat(self,**kw):
        self.calls.append(json.loads(kw['prompt']))
        if self.barrier:await self.barrier.wait()
        if self.fail:raise RuntimeError('offline failure')
        return NS(completion_text=json.dumps({'summary':'本批原信息及补全材料'}))
    async def start(self):
        result=self.r.maybe_start(self.scope,{'memorySummary':True,'naturalConversation':False},NS(text_chat=self.chat),lambda:True,self.metrics,self.var)
        if result:await self.r.task
        await asyncio.sleep(0)
        return result
    async def original_batch(self):
        # A real observed input plus 35 settled rounds. No inserted permit.
        at=time.time()-1000
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(self.scope,'12345678',self.event,'静默输入','',at))
        item=material.snapshots([message('silent'),{'role':'assistant','content':self.native}],self.scope)[0]
        item['reply']=''
        material.remember(self.db,self.scope,[item]);self.db.commit()
        for i in range(35):
            event=json.dumps([SCOPE,str(i)],separators=(',',':'))
            self.r.record(self.scope,'12345678',event,'本轮问题','确认实发',at+i+1,materials=material.snapshots([message(str(i))],self.scope))
        await self.start()
        cp=self.db.execute('SELECT id,summary_id,through_round_id,round_count,epoch,state FROM memory_reconstruction_checkpoints').fetchone()
        self.assertEqual(cp[-1],'pending_coverage')
        self.assertEqual(self.db.execute('SELECT round_id FROM memory_hold_rounds WHERE event=?',(self.event,)).fetchone(),(None,))
        return cp
    def bind_complete(self):
        # Use the producer contract, keeping immutable native text intact.
        messages=[message('silent'),{'role':'assistant','content':self.native}]
        message_provenance.bind_model_output(self.db,self.scope,SCOPE,'other-work','12345678',messages,1)
        message_provenance.settle_model_output(self.db,NS(scope=SCOPE,event_id='other-work',subject='12345678',status='sent',attempted=1,confirmed=1,planned_messages=1,confirmed_messages=1))
        message_provenance.settle_model_output(self.db,NS(scope=SCOPE,event_id='silent',subject='12345678',status='silent',attempted=0,confirmed=0,planned_messages=0,confirmed_messages=0))
        item=material.snapshots(messages,self.scope,db=self.db)[0]
        item['reply']=''
        material.remember(self.db,self.scope,[item]);self.db.commit()
        self.assertEqual(item['native_reply'],self.native)
        self.assertEqual(item['native_outputs'][0]['owner']['event_id'],'other-work')
        return item
    async def test_legacy_ambiguous_native_is_visible_but_never_paid(self):
        await self.original_batch();before=len(self.calls)
        self.assertFalse(await self.start())
        self.assertEqual(len(self.calls),before)
        self.assertGreater(self.metrics.get('summaryHoldUnsupported',0)+self.metrics.get('summaryHoldBindingMissing',0),0)
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'pending_coverage')
    async def test_complete_original_batch_repair_keeps_counts_and_is_idempotent(self):
        cp=await self.original_batch();self.bind_complete()
        before=self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0]
        watermark=self.db.execute('SELECT summary_watermark FROM memory_reflection_progress').fetchone()[0]
        self.assertTrue(await self.start())
        actual=self.db.execute('SELECT id,summary_id,through_round_id,round_count,epoch,state FROM memory_reconstruction_checkpoints').fetchone()
        self.assertEqual(actual,cp[:-1]+('available',))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT summary_watermark FROM memory_reflection_progress').fetchone()[0],watermark)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT summary_id FROM memory_summary_coverage WHERE event=?',(self.event,)).fetchone()[0],cp[1])
        self.assertFalse(await self.start())
        self.assertEqual(self.metrics['nullHoldBatchRepairs'],1)
    async def test_consumed_checkpoint_never_reactivates(self):
        cp=await self.original_batch();self.bind_complete()
        self.db.execute("UPDATE memory_reconstruction_checkpoints SET state='consumed'");self.db.commit();before=len(self.calls)
        self.assertFalse(await self.start());self.assertEqual(len(self.calls),before)
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],1)
    async def test_unknown_neighbor_delivery_blocks_without_model_call(self):
        await self.original_batch();self.bind_complete()
        message_provenance.settle_model_output(self.db,NS(scope=SCOPE,event_id='other-work',subject='12345678',status='unknown',attempted=1,confirmed=0,planned_messages=1,confirmed_messages=0))
        item=material.snapshots([message('silent'),{'role':'assistant','content':self.native}],self.scope,db=self.db)[0]
        material.remember(self.db,self.scope,[item]);self.db.commit();before=len(self.calls)
        self.assertFalse(await self.start());self.assertEqual(len(self.calls),before)
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'pending_coverage')
    async def test_transaction_failure_restart_preserves_original_product_and_material(self):
        cp=await self.original_batch();self.bind_complete()
        old=self.db.execute('SELECT text,claims,updated FROM memory_summaries WHERE id=?',(cp[1],)).fetchone()
        self.db.execute("CREATE TRIGGER fail_hold_delete BEFORE DELETE ON memory_hold_rounds BEGIN SELECT RAISE(ABORT,'failure injection'); END");self.db.commit()
        self.assertTrue(await self.start())
        self.assertEqual(self.db.execute('SELECT text,claims,updated FROM memory_summaries WHERE id=?',(cp[1],)).fetchone(),old)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material WHERE event=?',(self.event,)).fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'pending_coverage')
        self.store.close();self.store=BehaviorStore(self.path);self.r=self.store.reflection;self.db=self.store.db
        self.assertFalse(await self.start(),'persisted backoff survives restart')
        self.db.execute('DROP TRIGGER fail_hold_delete');self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
        self.assertTrue(await self.start())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'available')
    async def test_changed_checkpoint_while_awaiting_cannot_publish(self):
        cp=await self.original_batch();self.bind_complete();self.barrier=asyncio.Event()
        task=asyncio.create_task(self.start())
        for _ in range(20):
            await asyncio.sleep(0)
            if len(self.calls)>=2:break
        self.db.execute("UPDATE memory_reconstruction_checkpoints SET state='consumed'");self.db.commit()
        self.barrier.set();await task
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],1)
    async def test_later_big_merge_carries_consumed_checkpoint_without_new_credit(self):
        for start in (0,35,70):
            for i in range(start,start+35):
                event=json.dumps([SCOPE,str(i)],separators=(',',':'))
                self.r.record(self.scope,'12345678',event,'问题','真实回复',materials=material.snapshots([message(str(i))],self.scope))
            await self.start()
            if start==0:
                cp=self.db.execute('SELECT id,through_round_id,round_count,epoch FROM memory_reconstruction_checkpoints').fetchone()
                self.db.execute("UPDATE memory_reconstruction_checkpoints SET state='consumed' WHERE id=?",(cp[0],));self.db.commit()
        self.assertEqual(self.db.execute('SELECT id,through_round_id,round_count,epoch FROM memory_reconstruction_checkpoints WHERE id=?',(cp[0],)).fetchone(),cp)
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE id=?',(cp[0],)).fetchone()[0],'consumed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT kind FROM memory_summary_levels').fetchall(),[('big',)])

if __name__=='__main__':unittest.main()
