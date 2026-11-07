"""Memory/Work commit boundaries; isolated SQLite and controlled model responses."""
import asyncio, contextvars, sqlite3, sys, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from bot_pipeline import BotPipeline, Work, conversation_context
from autonomous_runtime import AutonomousRuntime
from memory_reflection import invalidate


class CommitBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_context_refreshes_facts_after_collect_or_media_await(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=BehaviorStore(Path(tmp)/'behavior.sqlite');now=time.time()
            try:
                st.memory.remember('private:12345678','12345678','饮料偏好','红茶','old',now)
                old=st.observe('private:12345678','12345678','喝什么','incoming',now)
                self.assertEqual(old['facts'][0]['value'],'红茶')
                # Admin/message edits can land during the pre-context await.
                st.memory.remember('private:12345678','12345678','饮料偏好','咖啡','new',now+1)
                st.db.commit()
                fresh=conversation_context(st,{'memory':True},'private:12345678','12345678','喝什么',now+2,state=old)
                self.assertEqual(fresh['state']['facts'][0]['value'],'咖啡')
                self.assertEqual(old['facts'][0]['value'],'红茶','do not mutate the ingress snapshot')
                invalidate(st.db,'private:12345678','12345678')
                st.db.execute("UPDATE facts SET active=0");st.db.commit()
                self.assertEqual(conversation_context(st,{'memory':True},'private:12345678','12345678','喝什么',now+3,state=old)['state']['facts'],[])
            finally:st.close()

    async def test_summary_rechecks_epoch_at_the_actual_write_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=BehaviorStore(Path(tmp)/'behavior.sqlite');scope='private:12345678'
            try:
                for i in range(35):st.reflection.record(scope,'12345678',str(i),'原问题'+str(i),'真实回答')
                async def chat(**kwargs):return NS(completion_text='迟到摘要')
                def decode(_):
                    # Deterministically inject deletion after the initial
                    # response check, before the summary write transaction.
                    invalidate(st.db,scope,'12345678');st.db.commit()
                    return {'summary':'已删除的旧聊天不应复活'}
                with patch('output_clean.clean',side_effect=lambda value:decode(value)['summary']) as cleaner:
                    self.assertTrue(st.reflection.maybe_start(scope,{'memory':True,'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=chat),lambda:True,{},contextvars.ContextVar('audit',default=None)))
                    await st.reflection.task
                cleaner.assert_called()
                self.assertEqual(st.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
                self.assertEqual(st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
            finally:st.close()

    async def test_batch_wait_is_not_a_completed_directed_execution(self):
        brain=object.__new__(AutonomousRuntime)
        brain.policies={'qq':{'config':{'mode':'active'}}};brain.feedbacks={}
        brain.events={'qq':{'eventId':'e','scope':'group:12345','senderId':'12345678','directed':True,'at':1}}
        event=NS(get_extra=lambda k,d=None:d)
        work=Work('bot','group:12345','12345678','99999','e','message',time.time(),{},ledger_owner=True)
        work.stage('admission');work.stop('batch_waiting','waiting')
        brain.completed('qq',event,work)
        self.assertNotIn('phase',brain.events['qq']);self.assertEqual(brain.feedbacks,{})
        work.stage('model_tools');work.stop('model_chose_silence','silent')
        brain.completed('qq',event,work)
        self.assertEqual(brain.events['qq']['phase'],'foreground_result')

    async def test_restart_settles_ephemeral_batch_but_keeps_durable_group_wait(self):
        db=sqlite3.connect(':memory:')
        try:
            BotPipeline(db)
            for event,reason in [('batch','batch_waiting'),('group','group_waiting')]:
                db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES('group:12345','12345678',?,?,'message','observe','waiting',?)",(event,time.time(),reason))
            db.commit();BotPipeline(db)
            self.assertEqual(dict(db.execute('SELECT event,status FROM pipeline_events')),{ 'batch':'interrupted','group':'waiting'})
        finally:db.close()

    async def test_error_after_a_confirmed_prefix_remains_partial(self):
        db=sqlite3.connect(':memory:')
        try:
            pipeline=BotPipeline(db)
            work=Work('bot','group:12345','12345678','99999','e','message',time.time(),{},ledger_owner=True)
            work.stage('dispatch');work.planned_messages=2
            work.attempted=work.confirmed=work.confirmed_messages=1
            # Preparing a later attachment can fail before another protocol
            # attempt. The first accepted message still exists externally.
            work.stop('media_preparation_failed','error');pipeline.finish(work,{})
            self.assertEqual(work.status,'partial');self.assertEqual(work.reason,'media_preparation_failed')
        finally:db.close()


if __name__=='__main__':unittest.main()
