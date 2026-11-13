"""Cleanup receipts are transactional; retired batches do not repeat hot scans."""
import json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot')]
import semantic_batch as batch
import semantic_runtime as runtime
import journal_storage
from context_protocol import model_json

CHANNEL='group:12345'


class PurgeCompletion(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');batch.initialize(self.db)
        self.db.executescript('''CREATE TABLE memory_pending(scope TEXT,event TEXT);
          CREATE TABLE memory_summary_coverage(scope TEXT,event TEXT);
          CREATE TABLE pipeline_scope_work(scope TEXT,event TEXT);
          CREATE TABLE pipeline_work_inputs(scope TEXT,event TEXT,status TEXT,payload TEXT);
          CREATE TABLE companion_signals(scope TEXT,event TEXT,kind TEXT,quote TEXT);
          CREATE TABLE memory_reconstruction_checkpoints(id INTEGER PRIMARY KEY,state TEXT,summary_id INTEGER);''')
        self.add_batch(1,['old']);self.db.commit()

    def tearDown(self):self.db.close()

    def add_batch(self,checkpoint,events):
        members=[batch.event_key('bot_shared',model_json([CHANNEL,event])) for event in events]
        self.db.execute("INSERT INTO memory_semantic_batches VALUES(?,'bot_shared','digest',?,0,'retired',0,0)",(checkpoint,model_json(members)))
        self.db.execute("INSERT INTO memory_reconstruction_checkpoints VALUES(?,'consumed',1)",(checkpoint,))
        self.db.executemany('INSERT INTO memory_pending VALUES(?,?)',[(CHANNEL,event) for event in events])

    def receipt(self):return self.db.execute('SELECT checkpoint_id FROM memory_semantic_purges').fetchall()

    def test_old_retired_batch_cleans_once_and_receipt_survives_reopen(self):
        # serialize/deserialize uses real persisted SQLite bytes, without files.
        with self.db:self.assertTrue(batch.purge(self.db,1))
        self.assertEqual(self.receipt(),[(1,)])
        data=self.db.serialize();self.db.close();self.db=sqlite3.connect(':memory:');self.db.deserialize(data)
        with patch.object(batch,'_purge',side_effect=AssertionError('repeated cleanup')):
            with self.db:self.assertTrue(batch.purge(self.db,1))
        self.assertEqual(self.db.execute('SELECT * FROM memory_pending').fetchall(),[])

    def test_preview_does_not_mark_complete_and_outer_rollback_restores_everything(self):
        self.db.execute('BEGIN')
        self.assertTrue(batch.purge(self.db,1,preview=True));self.assertEqual(self.receipt(),[])
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT event FROM memory_pending').fetchall(),[('old',)])
        self.assertTrue(batch.purge(self.db,1));self.assertEqual(self.receipt(),[(1,)])
        self.db.rollback()
        self.assertEqual(self.receipt(),[])
        self.assertEqual(self.db.execute('SELECT event FROM memory_pending').fetchall(),[('old',)])

    def test_archive_failure_rolls_back_cleanup_and_remains_retryable(self):
        with patch.object(journal_storage,'retire_archived_members',side_effect=ValueError('archive failure')):
            with self.assertRaisesRegex(ValueError,'archive failure'):batch.purge(self.db,1)
        self.assertEqual(self.receipt(),[])
        self.assertEqual(self.db.execute('SELECT event FROM memory_pending').fetchall(),[('old',)])
        with self.db:self.assertTrue(batch.purge(self.db,1))
        self.assertEqual(self.receipt(),[(1,)])

    def test_work_protection_prevents_receipt_until_ownership_released(self):
        self.db.execute("INSERT INTO pipeline_work_inputs VALUES(?,'old','owned','request')",(CHANNEL,));self.db.commit()
        with self.assertRaisesRegex(ValueError,'semantic_batch_input_still_owned'):batch.purge(self.db,1)
        self.assertEqual(self.receipt(),[])
        self.assertEqual(self.db.execute('SELECT event FROM memory_pending').fetchall(),[('old',)])
        self.db.execute("UPDATE pipeline_work_inputs SET status='settled'")
        with self.db:self.assertTrue(batch.purge(self.db,1))
        self.assertEqual(self.db.execute('SELECT payload FROM pipeline_work_inputs').fetchone(),(None,))

    def test_finish_skips_completed_batches_and_retries_only_failed_batch(self):
        st=SimpleNamespace(db=self.db,model_journals={})
        with patch.object(runtime,'_prune_hot_memory'),patch('semantic_media.drain',return_value={}),patch.object(runtime,'_issue') as issue:
            with patch.object(batch,'_purge',wraps=batch._purge) as purge:
                runtime.finish_semantic_batches(st,{},'bot_shared')
                runtime.finish_semantic_batches(st,{},'bot_shared')
                self.assertEqual(purge.call_count,1)
            issue.assert_called_once_with(st,'bot_shared',1,'retired',resolved=True)
            self.add_batch(2,['next']);self.db.commit()
            with patch.object(journal_storage,'retire_archived_members',side_effect=ValueError('archive failure')):
                self.assertEqual(runtime.finish_semantic_batches(st,{},'bot_shared')['pending'],1)
            with patch.object(batch,'_purge',wraps=batch._purge) as purge:
                runtime.finish_semantic_batches(st,{},'bot_shared')
                self.assertEqual([call.args[1] for call in purge.call_args_list],[2])
        self.assertEqual(self.receipt(),[(1,),(2,)])

    def test_feedback_and_coverage_keep_nonmembers_with_bounded_scans(self):
        self.db.execute('DELETE FROM memory_semantic_batches');self.db.execute('DELETE FROM memory_reconstruction_checkpoints')
        events=['old',*[f'old-{i}' for i in range(15)]]
        self.add_batch(1,events)
        for event in events:
            self.db.execute('INSERT INTO companion_signals VALUES(?,?,?,?)',(CHANNEL,'feedback-'+event,'dialogue_feedback',model_json({'reaction_event':event,'quote':'raw','score':2})))
        self.db.execute('INSERT INTO companion_signals VALUES(?,?,?,?)',(CHANNEL,'new-feedback','dialogue_feedback',model_json({'reaction_event':'new','quote':'keep'})))
        self.db.execute("INSERT INTO memory_pending VALUES(?,'new')",(CHANNEL,))
        self.db.execute("INSERT INTO pipeline_work_inputs VALUES(?,'owned','owned','keep')",(CHANNEL,))
        self.db.executemany('INSERT INTO memory_summary_coverage VALUES(?,?)',[(CHANNEL,e) for e in ('old','new','owned','obsolete')])
        self.db.commit();queries=[];self.db.set_trace_callback(queries.append)
        with self.db:batch.purge(self.db,1)
        self.db.set_trace_callback(None)
        feedback={event:json.loads(quote) for event,quote in self.db.execute('SELECT event,quote FROM companion_signals')}
        for event in events:
            self.assertEqual(feedback['feedback-'+event],{'reaction_event':'semantic:'+batch.event_key(CHANNEL,event),'score':2})
        self.assertEqual(feedback['new-feedback'],{'reaction_event':'new','quote':'keep'})
        self.assertEqual(set(self.db.execute('SELECT event FROM memory_summary_coverage')),{('new',),('owned',)})
        self.assertEqual(sum(query.startswith('SELECT event,quote FROM companion_signals') for query in queries),1)
        self.assertFalse(any(query.startswith('SELECT 1 FROM memory_pending WHERE') for query in queries))

    def test_fact_and_agreement_sources_update_matching_rows_without_per_event_table_scans(self):
        self.db.executescript('''CREATE TABLE facts(scope TEXT,event TEXT,value TEXT);
          CREATE TABLE memory_agreements(source_scope TEXT,source_event TEXT,detail TEXT);''')
        for table,columns in (('facts','scope,event,value'),('memory_agreements','source_scope,source_event,detail')):
            self.db.executemany('INSERT INTO '+table+'('+columns+') VALUES(?,?,?)',[(CHANNEL,'old','keep semantic product'),(CHANNEL,'new','keep new'),('group:67890','old','keep other channel')])
        self.db.commit();queries=[];self.db.set_trace_callback(queries.append)
        with self.db:batch.purge(self.db,1)
        self.db.set_trace_callback(None)
        semantic='semantic:'+batch.event_key(CHANNEL,'old')
        for table in ('facts','memory_agreements'):
            self.assertEqual(self.db.execute('SELECT * FROM '+table).fetchall(),[(CHANNEL,semantic,'keep semantic product'),(CHANNEL,'new','keep new'),('group:67890','old','keep other channel')])
            updates=[query for query in queries if query.startswith('UPDATE '+table+' SET')]
            self.assertEqual(len(updates),1);self.assertIn('WHERE rowid=',updates[0])


if __name__=='__main__':unittest.main()
