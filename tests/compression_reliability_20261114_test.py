"""Finite admission and local checkpoint recovery; no paid model or QQ calls."""
import asyncio,contextvars,copy,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parent)]
import semantic_runtime_test as fixture
from semantic_runtime import finish_semantic_batches


class Reliability(unittest.IsolatedAsyncioTestCase):
    setUp=fixture.RuntimeTests.setUp
    tearDown=fixture.RuntimeTests.tearDown

    async def test_finite_ingress_prefix_drains_while_later_inputs_wait(self):
        st=self.st;db=st.db;st.active=1
        db.execute("INSERT INTO pipeline_work_inputs(scope,event,status) VALUES('private:12345678','accepted','queued')");db.commit()
        entered=asyncio.Event();release=asyncio.Event()
        async def compress():
            async with st.compression_admission():entered.set();await release.wait()
        task=asyncio.create_task(compress());await asyncio.sleep(0)
        for i in range(50):db.execute("INSERT INTO pipeline_work_inputs(scope,event,status) VALUES('private:12345678',?,'pending')",('later-'+str(i),))
        db.commit()
        waiter=asyncio.create_task(st.wait_compression());await asyncio.sleep(0)
        self.assertFalse(entered.is_set());self.assertFalse(waiter.done())
        st.active=0
        db.execute("UPDATE pipeline_work_inputs SET status='settled' WHERE event='accepted'");db.commit();st.slot_changed.set()
        await asyncio.wait_for(entered.wait(),2)
        self.assertFalse(waiter.done())
        self.assertEqual(db.execute("SELECT count(*) FROM pipeline_work_inputs WHERE status='pending'").fetchone()[0],50)
        release.set();await task;await waiter
        self.assertIsNone(st.compression_boundary);self.assertIsNone(st.model_writer.owner)

    async def test_cancel_during_drain_restores_admission(self):
        st=self.st;st.active=1
        async def compress():
            async with st.compression_admission():self.fail('not idle')
        task=asyncio.create_task(compress());await asyncio.sleep(0)
        self.assertIsNotNone(st.compression_boundary)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertIsNone(st.compression_boundary);self.assertFalse(st.compression_active)
        st.active=0

    async def test_local_reserved_crash_window_recovers_exact_checkpoint(self):
        r=self.st.reflection;db=self.st.db
        permit=r.reserve_context_reconstruction(fixture.SCOPE,'semantic-retire:dead-process',checkpoint_id=1)
        self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'reserved')
        # Reopen durable Journal, as a fresh owner does after process death.
        from readonly_journal import ReadonlyJournal
        self.st.model_journals[fixture.SCOPE]=ReadonlyJournal(db,fixture.SCOPE)
        result=finish_semantic_batches(self.st,{},fixture.SCOPE)
        self.assertEqual(result['retired'],1,result)
        self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
        self.assertEqual(finish_semantic_batches(self.st,{},fixture.SCOPE)['retired'],0)

    async def test_generic_possibly_submitted_reservation_is_not_refunded(self):
        r=self.st.reflection;db=self.st.db
        self.assertTrue(r.reserve_context_reconstruction(fixture.SCOPE,'foreground-may-have-submitted',checkpoint_id=1))
        before=copy.deepcopy(self.st.model_journals[fixture.SCOPE].last.wire)
        self.assertEqual(finish_semantic_batches(self.st,{},fixture.SCOPE)['pending'],1)
        self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'reserved')
        self.assertEqual(self.st.model_journals[fixture.SCOPE].last.wire,before)

    async def test_committed_retirement_needs_no_provider_or_memory_switch(self):
        r=self.st.reflection
        r.binding=({'memory':False,'memorySummary':False},NS(),lambda:True,{},contextvars.ContextVar('recovery',default=None))
        r.admission=self.st.compression_admission
        r.checkpoint_callback=lambda scope:finish_semantic_batches(self.st,{},scope)
        self.assertTrue(r.retry());await r.task
        self.assertEqual(self.st.db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'retired')
        self.assertFalse(r.retry())

    async def test_local_failure_uses_persisted_backoff_without_model_spin(self):
        r=self.st.reflection;calls=[]
        r.binding=({'memory':False},NS(),lambda:True,{},contextvars.ContextVar('recovery',default=None))
        r.admission=self.st.compression_admission
        def fail(scope):calls.append(scope);raise ValueError('local failure')
        r.checkpoint_callback=fail
        self.assertTrue(r.retry());await r.task
        self.assertFalse(r.retry());self.assertEqual(len(calls),1)
        self.st.db.execute("UPDATE memory_reflection_retry SET next_at=0 WHERE scope='__retirement__'");self.st.db.commit()
        self.assertTrue(r.retry());await r.task;self.assertEqual(len(calls),2)

    async def test_admin_requires_retirement_and_media_cleanup_proof(self):
        from compression_admin import refresh_completion
        db=self.st.db
        db.execute("UPDATE memory_reconstruction_checkpoints SET scope='bot_shared'")
        db.execute("UPDATE memory_semantic_batches SET scope='bot_shared'");db.commit()
        job={'state':'pending_cleanup','result':{'ok':True,'checkpoint':{'id':1}}}
        refresh_completion(self.st,job);self.assertEqual(job['state'],'pending_cleanup')
        db.execute("UPDATE memory_reconstruction_checkpoints SET state='consumed'")
        db.execute("UPDATE memory_semantic_batches SET state='retired'");db.commit()
        import semantic_media
        semantic_media.initialize(db);db.commit()
        db.execute("INSERT INTO semantic_media_protocol_issues VALUES('ambiguous','unresolved-file','unresolved',0)");db.commit()
        refresh_completion(self.st,job);self.assertEqual(job['state'],'pending_cleanup')
        db.execute('DELETE FROM semantic_media_protocol_issues');db.commit()
        refresh_completion(self.st,job);self.assertEqual(job['state'],'completed')


if __name__=='__main__':unittest.main()
