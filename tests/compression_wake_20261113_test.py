"""A pre-HTTP capacity refusal resumes only after its exact Journal cutover."""
import asyncio,json,sqlite3,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parent)]
from bot_pipeline import BotPipeline
from ingress_settlement_20261113_test import message_event,LEASE
import initiative

class CompressionWake(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.p=BotPipeline(self.db)
        self.lease=dict(LEASE);self.event=message_event();self.work=self.p.bind(self.event,self.lease)
        self.work.stage('observe')
    def tearDown(self):self.db.close()
    def publish(self,key='bot_shared',checkpoint=1):
        self.db.execute('CREATE TABLE IF NOT EXISTS memory_semantic_batches(scope TEXT,checkpoint_id INTEGER,state TEXT)')
        self.db.execute("INSERT INTO memory_semantic_batches VALUES(?,?,'retired')",(key,checkpoint));self.db.commit()
    def status(self):return self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='101'").fetchone()[0]
    def wait(self,key='bot_shared'):
        self.assertTrue(self.p.wait_for_compression(self.work,key))
        self.work.stop('model_error','error');self.p.finish(self.work,{})
        self.assertEqual(self.status(),'waiting_compression')
    def test_refusal_waits_and_only_exact_journal_commit_releases_once(self):
        self.wait('private:12345')
        self.assertEqual(self.p.resume_compression_inputs('bot_shared'),0)
        self.assertEqual(self.status(),'waiting_compression')
        self.publish('private:12345')
        self.assertEqual(self.p.resume_compression_inputs('private:12345'),1)
        self.assertEqual(self.p.resume_compression_inputs('private:12345'),0)
        self.assertEqual(self.status(),'pending')
    def test_commit_before_original_finish_keeps_ready_input(self):
        self.assertTrue(self.p.wait_for_compression(self.work,'bot_shared'))
        self.publish()
        self.assertEqual(self.p.resume_compression_inputs('bot_shared'),1)
        self.work.stop('ContextCapacityExceeded','error');self.p.finish(self.work,{})
        self.assertEqual(self.status(),'pending')
        self.assertEqual(self.work.reason,'context_capacity_ready')
    def test_submitted_request_or_send_or_external_profile_action_never_replays(self):
        self.work.request_prepared=True
        self.assertFalse(self.p.wait_for_compression(self.work,'bot_shared'))
        self.work.request_prepared=False;self.work.attempted=1
        self.assertFalse(self.p.wait_for_compression(self.work,'bot_shared'))
        self.work.attempted=0
        self.db.execute('CREATE TABLE qq_action_attempts(event TEXT)')
        self.db.execute("INSERT INTO qq_action_attempts VALUES('101')")
        self.assertFalse(self.p.wait_for_compression(self.work,'bot_shared'))
    def test_old_unknown_failure_is_never_released(self):
        self.work.stop('ContextCapacityExceeded','error');self.p.finish(self.work,{})
        self.assertEqual(self.status(),'blocked_unknown')
        self.assertEqual(self.p.resume_compression_inputs('bot_shared'),0)
        self.assertEqual(self.status(),'blocked_unknown')
    def test_wait_survives_store_reopen_without_becoming_unknown(self):
        self.wait();self.p=BotPipeline(self.db)
        self.assertEqual(self.status(),'waiting_compression')
        self.publish()
        self.assertEqual(self.p.resume_compression_inputs('bot_shared'),1)
    async def test_existing_sweep_requeues_original_identity_then_bind_once(self):
        self.wait();self.publish();self.p.resume_compression_inputs('bot_shared')
        committed=[]
        async def convert(raw):return NS(timestamp=time.time(),raw_message=raw,message_id=raw['message_id'])
        platform=NS(meta=lambda:NS(id='qq'),convert_message=convert,create_event=lambda m:NS(message_obj=m),commit_event=committed.append)
        core=NS(event_queue=asyncio.Queue(),event_bus=NS(_pending_tasks={},stopped_platforms=set()),platform_manager=NS(get_insts=lambda:[platform]))
        store=NS(db=self.db,reflection=NS(checkpoint_issue=lambda *a,**k:None))
        with patch.dict(sys.modules,{'aiocqhttp':NS(Event=NS(from_payload=lambda raw:raw))}):
            self.assertTrue(await initiative.resume_scope_input(core,'qq',self.lease,store,{'qq':self.lease}))
        self.assertEqual(len(committed),1)
        self.assertEqual(committed[0].message_obj.message_id,'101')
        self.assertEqual(committed[0].message_obj.timestamp,self.event.message_obj.timestamp)
        resumed=self.p.bind(message_event(),self.lease)
        self.assertTrue(resumed.ledger_owner);self.assertEqual(resumed.event_id,'101')
        self.assertFalse(self.p.bind(message_event(),self.lease).ledger_owner)
        # Ordinary retained ingress uses this same sweep without a capacity
        # marker; it does not need another user message after compression.
        self.p.finish(resumed,{})
        ordinary=message_event('102');self.assertTrue(self.p.persist_input(ordinary,self.lease))
        with patch.dict(sys.modules,{'aiocqhttp':NS(Event=NS(from_payload=lambda raw:raw))}):
            self.assertTrue(await initiative.resume_scope_input(core,'qq',self.lease,store,{'qq':self.lease}))
        self.assertEqual([e.message_obj.message_id for e in committed],['101','102'])
    def test_old_retirement_cannot_release_new_wait_but_later_commit_can(self):
        self.publish(checkpoint=1);self.wait()
        self.assertEqual(self.p.resume_compression_inputs('bot_shared'),0)
        self.publish(checkpoint=2)
        self.p=BotPipeline(self.db)
        self.assertEqual(self.p.resume_compression_inputs('bot_shared'),1)

    def test_real_retirement_replay_closes_commit_to_wake_crash_gap(self):
        import semantic_runtime_test as fixture
        from semantic_runtime import finish_semantic_batches
        case=fixture.RuntimeTests();case.setUp()
        try:
            event=message_event();event.get_sender_id=lambda:'12345678';event.message_obj.raw_message['user_id']='12345678'
            work=case.st.pipeline.bind(event,self.lease)
            self.assertTrue(case.st.pipeline.wait_for_compression(work,fixture.SCOPE))
            case.st.pipeline.finish(work,{})
            with patch.object(case.st.pipeline,'resume_compression_inputs',side_effect=RuntimeError('simulated crash after durable retirement')):
                with self.assertRaises(RuntimeError):finish_semantic_batches(case.st,{},fixture.SCOPE)
            self.assertEqual(case.st.db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'retired')
            with patch('semantic_runtime.semantic_batch.purge',side_effect=ValueError('cleanup_failed')):
                result=finish_semantic_batches(case.st,{},fixture.SCOPE)
            self.assertEqual(result['pending'],1)
            self.assertEqual(case.st.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='101'").fetchone()[0],'waiting_compression')
            result=finish_semantic_batches(case.st,{},fixture.SCOPE)
            self.assertEqual(result['retired'],0)
            self.assertEqual(case.st.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='101'").fetchone()[0],'pending')
        finally:case.tearDown()

    async def test_current_tenant_identity_rechecked_at_existing_ingress(self):
        self.wait();self.publish();self.p.resume_compression_inputs('bot_shared')
        lease={**self.lease,'generation':2}
        store=NS(db=self.db,reflection=NS(checkpoint_issue=lambda *a,**k:None))
        core=NS(event_bus=NS(_pending_tasks={}))
        self.assertFalse(await initiative.resume_scope_input(core,'qq',lease,store,{'qq':lease}))
        self.assertEqual(self.status(),'blocked_evidence')

if __name__=='__main__':unittest.main()
