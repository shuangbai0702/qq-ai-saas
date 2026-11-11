"""Retired candidate indexes never retain raw text or own current memory."""
import copy
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from bot_pipeline import ContextBuilder,conversation_context
from context_protocol import READONLY_CURRENT,model_json
from readonly_journal import ReadonlyJournal
import behavior


SCOPE='private:12345'


class CandidateIndexRetirement(unittest.TestCase):
    def test_migration_restores_delete_policy_and_rolls_back_failed_ddl(self):
        for deny_drop in (False,True):
            with self.subTest(deny_drop=deny_drop),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'behavior.sqlite'
                st=BehaviorStore(path);st.close()
                db=sqlite3.connect(path)
                try:
                    db.executescript('''CREATE VIRTUAL TABLE memory_search USING fts5(terms);
                        CREATE TRIGGER search_fact_changed AFTER UPDATE ON facts BEGIN DELETE FROM memory_search; END;
                        CREATE TRIGGER search_fact_deleted AFTER DELETE ON facts BEGIN DELETE FROM memory_search; END;''')
                    db.execute('PRAGMA secure_delete=OFF')
                    original_reflection=behavior.Reflection
                    def reflection(connection):
                        value=original_reflection(connection)
                        # Semantic batch initialization currently enables this
                        # pragma. Isolate the retirement block's own policy.
                        connection.execute('PRAGMA secure_delete=OFF')
                        return value
                    if deny_drop:
                        db.set_authorizer(lambda action,name,*_:sqlite3.SQLITE_DENY
                            if action==sqlite3.SQLITE_DROP_VTABLE and name=='memory_search' else sqlite3.SQLITE_OK)
                    with patch('behavior.sqlite3.connect',return_value=db),patch('behavior.Reflection',side_effect=reflection):
                        if deny_drop:
                            with self.assertRaises(sqlite3.DatabaseError):BehaviorStore(path)
                        else:st=BehaviorStore(path)
                    db.set_authorizer(None)
                    self.assertEqual(db.execute('PRAGMA secure_delete').fetchone()[0],0)
                    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name IN ('memory_search','search_fact_changed','search_fact_deleted')")}
                    self.assertEqual(names,{'memory_search','search_fact_changed','search_fact_deleted'} if deny_drop else set())
                finally:db.close()

    def test_legacy_index_is_removed_without_changing_facts_or_committed_wire(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'behavior.sqlite'
            st=BehaviorStore(path)
            try:
                st.memory.remember(SCOPE,'12345','饮料','乌龙茶','fact',time.time())
                st.memory.remember('private:54321','54321','饮料','other person','other',time.time())
                st.db.commit()
                facts=st.db.execute('SELECT * FROM facts ORDER BY subject').fetchall()
                journal=ReadonlyJournal(st.db,SCOPE)
                prepared=journal.prepare([{'role':'user','content':'committed native input'},
                    {'role':'user','content':READONLY_CURRENT+'\n'+model_json({})}],None)
                self.assertTrue(journal.commit(prepared))
                saved=st.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchone()
                st.db.executescript('''CREATE VIRTUAL TABLE memory_search USING fts5(
                    kind UNINDEXED,scope UNINDEXED,subject UNINDEXED,key UNINDEXED,revision UNINDEXED,terms);
                    INSERT INTO memory_search VALUES('fact','private:12345','12345','饮料','1','retired raw body');
                    CREATE TRIGGER search_fact_changed AFTER UPDATE ON facts BEGIN DELETE FROM memory_search; END;
                    CREATE TRIGGER search_fact_deleted AFTER DELETE ON facts BEGIN DELETE FROM memory_search; END;
                    CREATE TRIGGER search_summary_changed AFTER UPDATE ON memory_summaries BEGIN DELETE FROM memory_search; END;
                    CREATE TRIGGER search_summary_deleted AFTER DELETE ON memory_summaries BEGIN DELETE FROM memory_search; END;''')
            finally:st.close()
            # Opening twice proves that retirement also supports a fresh store.
            for _ in range(2):
                st=BehaviorStore(path)
                try:
                    self.assertEqual(st.db.execute('SELECT * FROM facts ORDER BY subject').fetchall(),facts)
                    self.assertEqual(st.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchone(),saved)
                    leftovers=st.db.execute("SELECT name FROM sqlite_master WHERE name GLOB 'memory_search*' OR name IN ('search_fact_changed','search_fact_deleted','search_summary_changed','search_summary_deleted')").fetchall()
                    self.assertEqual(leftovers,[])
                    self.assertFalse(hasattr(st,'search'))
                    self.assertEqual(st.memory.recall(SCOPE,'12345','饮料',time.time())[0]['value'],'乌龙茶')
                    cfg={'memory':True,'memorySummary':False}
                    shared=conversation_context(st,cfg,SCOPE,'12345','unrelated wording',time.time())
                    builder=ContextBuilder({'scope':SCOPE,'sender_id':'12345'},{'scope':SCOPE})
                    builder.contribute('memory',shared)
                    foundation=builder.build()['memory_foundation']
                    self.assertEqual([x['value'] for x in foundation['facts']],['乌龙茶'])
                    self.assertEqual(foundation['facts'][0]['source_scope'],SCOPE)
                finally:st.close()

    def test_current_recall_obeys_correction_and_forget_after_index_retirement(self):
        with tempfile.TemporaryDirectory() as folder:
            st=BehaviorStore(Path(folder)/'behavior.sqlite')
            try:
                for event,text in [('a','记住：饮料=绿茶'),('b','更正记忆：饮料=红茶')]:
                    st.observe(SCOPE,'12345',text,event)
                self.assertEqual(st.memory.recall(SCOPE,'12345','饮料',time.time())[0]['value'],'红茶')
                self.assertEqual(st.memory.recall('private:54321','54321','饮料',time.time()),[])
                st.observe(SCOPE,'12345','忘记：饮料','c')
                self.assertEqual(st.memory.recall(SCOPE,'12345','饮料',time.time()),[])
                self.assertEqual(conversation_context(st,{'memorySummary':False},SCOPE,'12345','饮料',time.time())['memory_foundation']['facts'],[])
            finally:st.close()


class JournalCandidateSettlement(unittest.TestCase):
    def test_persistence_failure_refunds_only_a_provably_unsubmitted_candidate(self):
        for submitted in (False,True):
            with self.subTest(submitted=submitted):
                journal=ReadonlyJournal()
                prepared=journal.prepare([{'role':'user','content':'real input'},
                    {'role':'user','content':READONLY_CURRENT+'\n{}'}],None)
                journal.db=object()
                prepared.reconstruction_authority=NS(db=journal.db)
                prepared.reconstruction_permit={'id':1}
                before=copy.deepcopy(prepared.wire)
                with patch.object(journal,'_persist',return_value=False),patch.object(journal,'_settle',return_value=True) as settle:
                    result=journal.retain_submitted(prepared) if submitted else journal.commit(prepared)
                self.assertFalse(result)
                settle.assert_called_once_with(prepared,submitted)
                self.assertIs(journal.last,prepared)
                self.assertEqual(journal.last.wire,before)
                self.assertEqual(journal.retired_reason,'persistence_unavailable')


if __name__=='__main__':unittest.main()
