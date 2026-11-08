"""Offline task-start and post-response ownership checks for expression learning."""
import asyncio,contextvars,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from social import SocialStore,maybe_learn


class LearningLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_queued_jobs_make_no_request_and_reserve_no_cooldown(self):
        for change,expected in [('close','learning_store_closed'),('lease','learning_lease_replaced'),('disable','learning_disabled'),('epoch','learning_epoch_changed'),('auth','learning_auth_paused')]:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                db=sqlite3.connect(':memory:');store=SocialStore(db,root)
                for i in range(12):store.observe('group:1',str(i%2),'这波有节目效果',str(i))
                state={'current':True,'auth':False};cfg={'styleLearning':True};calls=[];reasons=[];metrics={}
                async def model(**_):calls.append(True);return NS(completion_text='{"patterns":[]}')
                with patch('autonomy.auth_paused',side_effect=lambda *_:state['auth']),patch('social.discarded',side_effect=reasons.append):
                    maybe_learn(store,'group:1',cfg,NS(text_chat=model),lambda:state['current'],metrics,contextvars.ContextVar('style-event',default=None))
                    if change=='close':store.closed=True
                    elif change=='lease':state['current']=False
                    elif change=='disable':cfg['styleLearning']=False
                    elif change=='epoch':db.execute('UPDATE social_control SET epoch=epoch+1');db.commit()
                    elif change=='auth':state['auth']=True
                    await store.task
                self.assertEqual(calls,[]);self.assertEqual(reasons,[expected]);self.assertNotIn('learningRequests',metrics)
                self.assertEqual(db.execute('SELECT count(*) FROM social_learning').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM social_samples').fetchone()[0],12)
                store.close();db.close()

    async def test_late_results_are_explicitly_discarded_without_consuming_samples(self):
        for change,expected in [('close','learning_store_closed'),('lease','learning_lease_replaced'),('disable','learning_disabled'),('epoch','learning_epoch_changed')]:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                db=sqlite3.connect(':memory:');store=SocialStore(db,root)
                for i in range(12):store.observe('group:1',str(i%2),'这波有节目效果',str(i))
                entered=asyncio.Event();release=asyncio.Event();state={'current':True};cfg={'styleLearning':True};reasons=[];metrics={}
                async def model(**_):entered.set();await release.wait();return NS(completion_text='{"patterns":[]}')
                with patch('social.discarded',side_effect=reasons.append),patch.object(store,'accept_learning',wraps=store.accept_learning) as accept:
                    maybe_learn(store,'group:1',cfg,NS(text_chat=model),lambda:state['current'],metrics,contextvars.ContextVar('style-event',default=None))
                    await entered.wait()
                    if change=='close':store.closed=True
                    elif change=='lease':state['current']=False
                    elif change=='disable':cfg['styleLearning']=False
                    else:db.execute('UPDATE social_control SET epoch=epoch+1');db.commit()
                    release.set();await store.task;accept.assert_not_called()
                self.assertEqual(reasons,[expected]);self.assertEqual(metrics['learningRequests'],1)
                self.assertEqual(db.execute('SELECT watermark FROM social_learning').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM social_samples').fetchone()[0],12)
                store.close();db.close()

    async def test_task_slot_still_serializes_and_valid_empty_learning_consumes_batch(self):
        with tempfile.TemporaryDirectory() as root:
            db=sqlite3.connect(':memory:');store=SocialStore(db,root)
            for i in range(12):store.observe('group:1',str(i%2),'有节目效果',str(i))
            entered=asyncio.Event();release=asyncio.Event();calls=[];adoptions=[];cfg={'styleLearning':True};metrics={}
            async def model(**_):calls.append(True);entered.set();await release.wait();return NS(completion_text='{"patterns":[]}')
            provider=NS(text_chat=model);event=contextvars.ContextVar('style-event',default='original')
            with patch('social.adopted',side_effect=adoptions.append):
                maybe_learn(store,'group:1',cfg,provider,lambda:True,metrics,event)
                first=store.task
                maybe_learn(store,'group:1',cfg,provider,lambda:True,metrics,event)
                self.assertIs(store.task,first);self.assertEqual(db.execute('SELECT count(*) FROM social_learning').fetchone()[0],0)
                await entered.wait();self.assertEqual(db.execute('SELECT count(*) FROM social_learning').fetchone()[0],1)
                release.set();await first
            self.assertEqual(calls,[True]);self.assertEqual(adoptions,['learning_applied']);self.assertEqual(event.get(),'original')
            self.assertGreater(db.execute('SELECT watermark FROM social_learning').fetchone()[0],0)
            self.assertEqual(store.learning_batch('group:1'),[]);store.close();db.close()


if __name__=='__main__':unittest.main()
