"""Whole-batch recovery replaces old NULL-Hold/Coverage promotion fixtures.

The old retry branch was disabled before this change. These cases retain its
safety goals at the actual semantic entry: exact 35-round credit, observed
inputs without credit, honest missing material, rollback/restart, concurrent
changes, stale configuration and consumed-permit non-reactivation.
"""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from summary_material_test import message,SCOPE
import summary_material as material
import message_provenance

class SemanticObservedRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.r=self.store.reflection
        self.r.shared_context=True
        self.var=contextvars.ContextVar('null_hold',default=None);self.calls=[];self.metrics={}
        self.scope='bot_shared';self.event=json.dumps([SCOPE,'silent'],separators=(',',':'))
        self.native='其他Work的原稿';self.fail_model=False;self.barrier=None;self.started=None;self.live=True
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def chat(self,**kw):
        self.calls.append(json.loads(kw['prompt']))
        if self.started:self.started.set()
        if self.barrier:await self.barrier.wait()
        if self.fail_model:raise RuntimeError('offline failure')
        return NS(completion_text=json.dumps({'summary':'本批可见原信息及补全材料，未知发送仍未知'}))
    def bind(self):
        self.r.binding=({'memorySummary':True,'naturalConversation':False},NS(text_chat=self.chat),lambda:self.live,self.metrics,self.var)
    async def start(self):
        self.bind();result=self.r.retry()
        if result:await self.r.task
        return result
    def seed(self,count=35):
        at=time.time()-1000
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(self.scope,'12345678',self.event,'静默输入','',at))
        item=material.snapshots([message('silent'),{'role':'assistant','content':self.native}],self.scope)[0]
        item['reply']='';material.remember(self.db,self.scope,[item]);self.db.commit()
        for i in range(count):
            rows=material.snapshots([message(str(i)),{'role':'assistant','content':'确认实发'}],self.scope)
            self.r.record(self.scope,'12345678',rows[0]['event'],'本轮问题','确认实发',at+i+1,materials=rows)
    def checkpoint(self):
        return self.db.execute('SELECT id,summary_id,through_round_id,round_count,epoch,state FROM memory_reconstruction_checkpoints ORDER BY id DESC LIMIT 1').fetchone()
    def bind_complete(self,status='sent'):
        messages=[message('silent'),{'role':'assistant','content':self.native}]
        message_provenance.bind_model_output(self.db,self.scope,SCOPE,'other-work','12345678',messages,1)
        message_provenance.settle_model_output(self.db,NS(scope=SCOPE,event_id='other-work',subject='12345678',status=status,attempted=1,confirmed=int(status=='sent'),planned_messages=1,confirmed_messages=int(status=='sent')))
        message_provenance.settle_model_output(self.db,NS(scope=SCOPE,event_id='silent',subject='12345678',status='silent',attempted=0,confirmed=0,planned_messages=0,confirmed_messages=0))
        item=material.snapshots(messages,self.scope,db=self.db)[0];item['reply']=''
        material.remember(self.db,self.scope,[item]);self.db.commit()
        self.assertEqual(item['native_reply'],self.native)
        self.assertEqual(item['native_outputs'][0]['owner']['event_id'],'other-work')
        return item
    async def test_ambiguous_native_is_visible_once_without_round_or_coverage_credit(self):
        self.seed();self.assertTrue(await self.start())
        cp=self.checkpoint();self.assertEqual(cp[3:],(35,0,'available'))
        self.assertIn(self.native,json.dumps(self.calls,ensure_ascii=False))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_coverage').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_round_material WHERE event=?',(self.event,)).fetchone(),'raw stays until Journal retirement')
        before=len(self.calls);self.assertFalse(await self.start());self.assertEqual(len(self.calls),before)
    async def test_proven_output_keeps_original_work_counts_and_idempotence(self):
        self.seed();self.bind_complete();self.assertTrue(await self.start())
        cp=self.checkpoint();self.assertEqual(cp[3],35);self.assertEqual(cp[-1],'available')
        wire=json.dumps(self.calls,ensure_ascii=False)
        self.assertIn('other-work',wire);self.assertIn(self.native,wire)
        watermark=self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?',(self.scope,)).fetchone()[0]
        self.assertEqual(watermark,35)
        self.assertFalse(await self.start());self.assertEqual(len(self.calls),1);self.assertEqual(self.checkpoint(),cp)
    async def test_consumed_checkpoint_never_reactivates(self):
        self.seed();self.bind_complete();await self.start();cp=self.checkpoint()
        permit=self.r.reserve_context_reconstruction(self.scope,'one-real-owner')
        self.assertIsNotNone(permit);self.assertTrue(self.r.settle_context_reconstruction(permit,True))
        self.assertFalse(await self.start());self.r._promote_pending_checkpoints(self.scope);self.db.commit()
        self.assertEqual(self.checkpoint(),cp[:-1]+('consumed',));self.assertEqual(len(self.calls),1)
        self.assertIsNone(self.r.reserve_context_reconstruction(self.scope,'never-reactivate'))
    async def test_unknown_neighbor_output_is_labeled_without_becoming_confirmed_credit(self):
        self.seed();self.bind_complete('unknown');await self.start()
        self.assertEqual(self.checkpoint()[3],35)
        wire=json.dumps(self.calls,ensure_ascii=False)
        self.assertIn('other-work',wire);self.assertIn('unknown',wire)
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_rounds WHERE event=?',(self.event,)).fetchone())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_coverage').fetchone()[0],0)
    async def test_transaction_failure_restart_preserves_material_and_retry(self):
        self.seed();self.bind_complete()
        self.db.execute("CREATE TRIGGER fail_semantic_commit BEFORE INSERT ON memory_semantic_batches BEGIN SELECT RAISE(ABORT,'failure injection'); END");self.db.commit()
        self.assertTrue(await self.start());self.assertIsNone(self.checkpoint())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],36)
        self.store.close();self.store=BehaviorStore(self.path);self.r=self.store.reflection;self.db=self.store.db;self.r.shared_context=True
        self.assertFalse(await self.start(),'persisted backoff survives restart')
        self.db.execute('DROP TRIGGER fail_semantic_commit');self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
        self.assertTrue(await self.start());self.assertEqual(self.checkpoint()[-1],'available')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
    async def test_changed_material_while_awaiting_cannot_publish(self):
        self.seed();self.bind_complete();self.barrier=asyncio.Event();self.started=asyncio.Event()
        task=asyncio.create_task(self.start());await self.started.wait()
        raw=self.db.execute('SELECT material FROM memory_round_material WHERE event=?',(self.event,)).fetchone()[0]
        value=json.loads(raw);value['reply']='late conflicting material'
        self.db.execute('UPDATE memory_round_material SET material=? WHERE event=?',(json.dumps(value),self.event));self.db.commit()
        self.barrier.set();await task
        self.assertIsNone(self.checkpoint());self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertEqual(self.metrics['summaryFailures'],1)
    async def test_manual_configuration_change_is_not_success(self):
        self.seed();self.bind_complete();self.barrier=asyncio.Event();self.started=asyncio.Event();self.bind()
        self.assertTrue(self.r.force_compress_once()['started']);await self.started.wait()
        self.live=False;self.barrier.set();result=await self.r.task
        self.assertFalse(result['ok']);self.assertIsNone(self.checkpoint())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],36)
    async def test_missing_original_is_explicit_and_does_not_bypass_35_round_gate(self):
        self.seed(34)
        self.db.execute("INSERT INTO memory_pending VALUES(?,?,?,?,?,?)",('group:55555','87654321','missing','actual surviving fragment','',time.time()-2000));self.db.commit()
        self.assertFalse(await self.start());self.assertEqual(self.calls,[])
        self.r.binding=None
        rows=material.snapshots([message('34'),{'role':'assistant','content':'确认实发'}],self.scope)
        self.r.record(self.scope,'12345678',rows[0]['event'],'本轮问题','确认实发',materials=rows)
        self.db.execute('DELETE FROM memory_reflection_retry');self.db.commit();await self.start()
        self.assertEqual(self.checkpoint()[3],35);self.assertEqual(len(self.calls),1)
        self.assertIn('actual surviving fragment',json.dumps(self.calls,ensure_ascii=False))
        self.assertEqual(self.db.execute('SELECT missing_count FROM memory_semantic_discard_audit').fetchone()[0],1)

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
