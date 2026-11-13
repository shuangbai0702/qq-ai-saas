"""Independent lawful checkpoints commit together or leave both Journals intact."""
import contextvars,copy,sys,tempfile,time,unittest,uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from context_protocol import SOURCE,model_json
from readonly_journal import ReadonlyJournal,Prepared
from summary_material import snapshots
from summary_stream_fixture import summary_provider


class Cohort(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.st=BehaviorStore(Path(self.tmp.name)/'one.sqlite')
        self.cfg={'memory':True,'memorySummary':True,'naturalConversation':False}
        self.scopes=('private:12345678','group:55555');self.calls=0
        async def chat(**kwargs):
            self.calls+=1
            return NS(completion_text=model_json({'summary':'保留经确认的对话记忆'}))
        r=self.st.reflection;r.checkpoint_callback=None
        r.binding=(self.cfg,summary_provider(chat),lambda:True,{},contextvars.ContextVar('cohort',default=None))
        for scope in self.scopes:
            base=[{'role':'system','content':'EXACT_SYSTEM'}]
            for i in range(35):
                event='original-'+str(i)
                user={'role':'user','content':SOURCE+model_json({'scope':scope,'sender_id':'12345678','event_id':event})+'\nactual text '+str(i)}
                messages=[user,{'role':'assistant','content':'actual reply '+str(i)}]
                r.record(scope,'12345678',event,'actual text '+str(i),'actual reply '+str(i),now=100+i,materials=snapshots(messages,scope))
                base.extend(messages)
            peer=next(s for s in self.scopes if s!=scope)
            state={'recent_cross_session_messages':[{'scope':peer,'event_id':'original-0','text':'actual text 0'}]}
            journal=ReadonlyJournal(self.st.db,scope);journal.serial=1
            prepared=Prepared(1,None,copy.deepcopy(base),copy.deepcopy(base),state,1,time.time(),'initial')
            self.assertTrue(journal.commit(prepared));self.st.model_journals[scope]=journal
        # Summary-only fixture; no production/provider calls.
        for scope in self.scopes:
            result=r.force_compress_once(scope,allow_partial=True,administrator='fixture',request_id=str(uuid.uuid4()))
            self.assertTrue(result['started'],result)
            await r.task
        self.assertEqual(self.calls,2)
        self.assertEqual(self.st.db.execute("SELECT count(*) FROM memory_semantic_batches WHERE state='committed'").fetchone()[0],2)

    async def asyncTearDown(self):
        self.st.close();self.tmp.cleanup()

    async def test_cross_scope_windows_retire_without_another_model_call(self):
        from semantic_runtime import finish_semantic_batches
        result=finish_semantic_batches(self.st,self.cfg,self.scopes[0])
        self.assertEqual(result['retired'],2,result)
        self.assertEqual(self.calls,2)
        self.assertEqual(self.st.db.execute("SELECT count(*) FROM memory_reconstruction_checkpoints WHERE state='consumed'").fetchone()[0],2)
        for scope in self.scopes:
            journal=ReadonlyJournal(self.st.db,scope)
            self.assertEqual(journal.last.base,[{'role':'system','content':'EXACT_SYSTEM'}])
            self.assertEqual(journal.last.state.get('recent_cross_session_messages',[]),[])

    async def test_second_write_failure_rolls_back_both_scopes_and_permits(self):
        from semantic_runtime import _finish_cohort
        rows=self.st.db.execute('SELECT checkpoint_id,b.scope,summary_id FROM memory_semantic_batches b JOIN memory_reconstruction_checkpoints ON id=checkpoint_id ORDER BY checkpoint_id').fetchall()
        before=self.st.db.execute('SELECT scope,payload,sha FROM fishbot_readonly_journals ORDER BY scope').fetchall()
        previous={s:j.last for s,j in self.st.model_journals.items()};persist=ReadonlyJournal._persist;calls=0
        def fail(journal,*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('injected second journal write failure')
            return persist(journal,*args,**kwargs)
        with patch.object(ReadonlyJournal,'_persist',fail):
            with self.assertRaises(OSError):_finish_cohort(self.st,self.cfg,rows)
        self.assertEqual(before,self.st.db.execute('SELECT scope,payload,sha FROM fishbot_readonly_journals ORDER BY scope').fetchall())
        self.assertEqual(self.st.db.execute("SELECT count(*) FROM memory_reconstruction_checkpoints WHERE state='available'").fetchone()[0],2)
        for scope,journal in self.st.model_journals.items():
            self.assertIs(journal.last,previous[scope]);self.assertIsNone(journal._staged)


if __name__=='__main__':unittest.main()
