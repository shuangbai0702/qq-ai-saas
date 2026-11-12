"""Style learning shares the summary task, transaction and lifecycle owner."""
import asyncio,json,sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from style_learning_summary_fixture import SummaryLearningFixture


class LearningLifecycleTests(SummaryLearningFixture,unittest.IsolatedAsyncioTestCase):
    def fresh(self,name):
        self.store.close()
        from behavior import BehaviorStore
        self.path=Path(self.temporary.name)/(name+'.sqlite')
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.ref=self.store.reflection
        self.state['current']=True;self.cfg['styleLearning']=True;self.calls.clear()

    async def test_stale_queued_jobs_make_no_request_and_reserve_no_cooldown(self):
        # Disabling style alone leaves the authorized summary request intact.
        for change in ('close','lease','epoch'):
            with self.subTest(change=change):
                self.fresh('queued-'+change);self.record();self.start()
                if change=='close':self.ref.closed=True
                elif change=='lease':self.state['current']=False
                else:self.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1');self.db.commit()
                await self.finish()
                self.assertEqual(self.calls,[]);self.assertEqual(self.social_attempts(),0)
                self.assertEqual(self.sample_count(),35);self.assertEqual(self.patterns(),[])

    async def test_late_results_are_explicitly_discarded_without_consuming_samples(self):
        for change,expected in [('close',None),('lease',None),('disable','learning_disabled'),('epoch','learning_epoch_changed')]:
            with self.subTest(change=change):
                self.fresh('late-'+change);self.record();entered=asyncio.Event();release=asyncio.Event()
                async def model(**kw):entered.set();await release.wait();return self.result(kw)
                with patch('social.accept_summary_learning',wraps=__import__('social').accept_summary_learning) as accept:
                    self.start(model);await entered.wait()
                    if change=='close':self.ref.closed=True
                    elif change=='lease':self.state['current']=False
                    elif change=='disable':self.cfg['styleLearning']=False
                    else:self.db.execute('UPDATE social_control SET epoch=epoch+1');self.db.commit()
                    release.set();await self.finish();accept.assert_not_called()
                self.assertEqual(self.patterns(),[]);self.assertEqual(self.social_attempts(),0)
                if expected:
                    self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
                    receipt=self.db.execute("SELECT decision,reason FROM memory_operation_audit_details WHERE category='learning' ORDER BY rowid DESC LIMIT 1").fetchone()
                    self.assertEqual(receipt,('rejected',expected))
                else:self.assertEqual(self.sample_count(),35)

    async def test_task_slot_serializes_and_empty_result_consumes_only_with_summary(self):
        self.record();entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def model(**kw):
            calls.append(kw);entered.set();await release.wait()
            from types import SimpleNamespace
            return SimpleNamespace(completion_text=json.dumps({'summary':'真实交流','learning':[]}))
        self.assertTrue(self.start(model));first=self.ref.task
        self.assertFalse(self.start(model));self.assertIs(self.ref.task,first)
        self.assertEqual(self.social_attempts(),0)
        await entered.wait();release.set();await first
        self.assertEqual(len(calls),1);self.assertEqual(self.event.get(),'original')
        self.assertEqual(self.social_attempts(),0);self.assertEqual(self.patterns(),[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone()[0],35)
        # The durable checkpoint consumes the batch; original rows remain until
        # the Journal adopts it, so learning cannot delete those rows itself.
        self.assertFalse(self.start(model));self.assertEqual(len(calls),1)

    async def test_before_35_no_request_then_one_summary_learns_two_real_senders(self):
        self.record(count=34)
        self.assertFalse(self.start());await self.finish()
        self.assertEqual(self.calls,[]);self.assertIsNone(self.store.social.task)
        self.record(start=34,count=1);self.assertTrue(self.start());await self.finish()
        self.assertEqual(len(self.calls),1);self.assertNotIn('learningRequests',self.metrics)
        batch=json.loads(self.calls[0]['prompt'])['style_learning']
        self.assertEqual(len(batch),1);self.assertEqual(len(batch[0]['samples']),35)
        self.assertEqual({row['sender'] for row in batch[0]['samples']},{'12345678','23456789'})
        self.assertEqual(self.patterns(),[(self.scope,'有节目效果')])

    async def test_shared_batch_uses_original_group_and_excludes_private_and_unoffered(self):
        self.scope='bot_shared'
        self.record(origins=['group:12345','group:12345','private:12345678'])
        self.store.social.observe('group:67890','34567890','有节目效果','not-in-batch')
        self.start();await self.finish()
        batches=json.loads(self.calls[0]['prompt'])['style_learning']
        self.assertEqual([row['scope'] for row in batches],['group:12345'])
        self.assertEqual(len(batches[0]['samples']),24)
        self.assertEqual(self.patterns(),[('group:12345','有节目效果')])

    async def test_disabled_before_request_keeps_summary_and_omits_style_data(self):
        self.record();self.start();self.cfg['styleLearning']=False;await self.finish()
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('style_learning',json.loads(self.calls[0]['prompt']))
        self.assertEqual(self.patterns(),[]);self.assertEqual(self.social_attempts(),0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)

    async def test_database_failure_at_style_fence_rolls_back_summary_and_learning(self):
        self.record();entered=asyncio.Event();release=asyncio.Event()
        async def model(**kw):entered.set();await release.wait();return self.result(kw)
        self.start(model);await entered.wait()
        def authorize(action,table,*_):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table=='social_control' else sqlite3.SQLITE_OK
        self.db.set_authorizer(authorize)
        try:release.set();await self.finish()
        finally:self.db.set_authorizer(None)
        self.assertEqual(self.metrics['summaryFailures'],1)
        self.assertEqual(self.patterns(),[]);self.assertEqual(self.sample_count(),35)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)


if __name__=='__main__':unittest.main()
