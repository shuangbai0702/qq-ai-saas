"""Legal local checkpoints offer remaining fragments, not invented originals."""
import contextvars,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parent)]
import semantic_batch_summary_test as fixture
from summary_material_test import SCOPE
import semantic_batch,semantic_collection


class LocalMaterialRecovery(unittest.IsolatedAsyncioTestCase):
    setUp=fixture.SemanticSummary.setUp
    tearDown=fixture.SemanticSummary.tearDown
    record=fixture.SemanticSummary.record
    chat=fixture.SemanticSummary.chat
    run_summary=fixture.SemanticSummary.run_summary
    checkpoint=fixture.SemanticSummary.checkpoint

    async def test_missing_original_is_offered_and_retired_only_after_replacement(self):
        self.record(35)
        self.db.execute('DELETE FROM memory_round_material WHERE scope=? AND event=?',(SCOPE,'0'))
        self.db.commit()
        self.assertTrue(await self.run_summary())
        self.assertIsNotNone(self.checkpoint())
        prompt=json.loads(self.calls[0]['prompt'])
        self.assertEqual(len(prompt['turns']),34)
        missing=prompt['incomplete_material'][0]
        self.assertEqual(missing['event'],'0')
        self.assertEqual(missing['missing'],'full_original')
        self.assertTrue(missing['available_text'])
        cp=self.checkpoint()[0]
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_pending WHERE scope=? AND event=?',(SCOPE,'0')).fetchone())
        with self.db:
            self.db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=?",(cp,))
            semantic_batch.purge(self.db,cp)
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_pending WHERE scope=? AND event=?',(SCOPE,'0')).fetchone())

    async def test_raw_fragment_change_during_model_request_prevents_commit(self):
        self.record(35)
        self.db.execute('DELETE FROM memory_round_material WHERE scope=? AND event=?',(SCOPE,'0'));self.db.commit()
        async def changed(**kw):
            self.db.execute('UPDATE memory_pending SET question=? WHERE scope=? AND event=?',('recovered new fragment',SCOPE,'0'));self.db.commit()
            return NS(completion_text='{"summary":"valid summary"}')
        self.r.binding=({'memory':True,'memorySummary':True,'naturalConversation':False},NS(text_chat=changed),lambda:True,self.metrics,contextvars.ContextVar('local_cas',default=None))
        self.assertTrue(self.r.retry());await self.r.task
        self.assertIsNone(self.checkpoint())
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_pending WHERE scope=? AND event=?',(SCOPE,'0')).fetchone())

    def test_valid_incomplete_capture_cannot_change_sender(self):
        self.record(1)
        saved=self.db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(SCOPE,'0')).fetchone()
        material=json.loads(saved[0]);material['complete']=False
        self.db.execute('UPDATE memory_round_material SET material=? WHERE scope=? AND event=?',(json.dumps(material),SCOPE,'0'))
        row={'event':'0','sender_id':'87654321','user':'fragment','bot':''}
        with self.assertRaisesRegex(ValueError,'sender_conflict'):
            semantic_collection.collect_local(self.db,SCOPE,[row])

    async def test_unsupported_capture_identity_conflicts_prevent_model_and_commit(self):
        self.record(35)
        saved=self.db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(SCOPE,'0')).fetchone()
        original=json.loads(saved[0])
        mutations=[{'scope':'group:55555'},{'event':'different-event'},{'subject':'87654321'},
                   {'text':original['text'].replace('12345678','87654321')}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.r.binding=None
                self.db.execute('DELETE FROM memory_reflection_retry')
                invalid={**original,'version':999,**mutation}
                self.db.execute('UPDATE memory_round_material SET material=? WHERE scope=? AND event=?',(json.dumps(invalid),SCOPE,'0'))
                self.db.commit()
                self.assertTrue(await self.run_summary())
                self.assertEqual(self.calls,[])
                self.assertIsNone(self.checkpoint())
                self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds WHERE scope=?',(SCOPE,)).fetchone()[0],35)
                self.assertEqual(json.loads(self.db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(SCOPE,'0')).fetchone()[0]),invalid)

    def test_local_stamp_is_scope_bound_and_tracks_late_ownership(self):
        import sqlite3
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE fishbot_readonly_journals(scope TEXT,sha TEXT)')
            db.execute('CREATE TABLE pipeline_work_inputs(scope TEXT,event TEXT,status TEXT)')
            db.execute('INSERT INTO fishbot_readonly_journals VALUES(?,?)',(json.dumps(SCOPE),'current'))
            db.execute('INSERT INTO fishbot_readonly_journals VALUES(?,?)',(json.dumps('private:87654321'),'unrelated'))
            discarded=[{'event':'0'}]
            before=semantic_collection.unavailable_stamp(db,discarded,scope=SCOPE)
            db.execute('UPDATE fishbot_readonly_journals SET sha=? WHERE scope=?',('changed',json.dumps('private:87654321')))
            self.assertEqual(semantic_collection.unavailable_stamp(db,discarded,scope=SCOPE),before)
            db.execute('INSERT INTO pipeline_work_inputs VALUES(?,?,?)',(SCOPE,'0','owned'))
            self.assertNotEqual(semantic_collection.unavailable_stamp(db,discarded,scope=SCOPE),before)
            with self.assertRaisesRegex(ValueError,'input_still_owned'):
                semantic_collection.collect_local(db,SCOPE,[{'event':'0'}])
            db.execute('DELETE FROM pipeline_work_inputs')
            self.assertEqual(semantic_collection.unavailable_stamp(db,discarded,scope=SCOPE),before)
            db.execute('UPDATE fishbot_readonly_journals SET sha=? WHERE scope=?',('changed-current',json.dumps(SCOPE)))
            self.assertNotEqual(semantic_collection.unavailable_stamp(db,discarded,scope=SCOPE),before)
        finally:db.close()

    async def test_all_missing_captures_preserve_author_memory_and_summary_visibility(self):
        from memory_scope import fact_rows,summary_rows
        scope='group:55555';person='12345678'
        for i in range(35):
            self.r.record(scope,person,str(i),'我现在喜欢喝咖啡，保留这段文字。','知道了。',now=100+i,materials=[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
        async def chat(**kw):
            self.calls.append(kw);prompt=json.loads(kw['prompt'])
            self.assertEqual(prompt['turns'],[]);self.assertEqual(prompt['observed_requests'],[])
            self.assertEqual(len(prompt['incomplete_material']),35)
            self.assertEqual(prompt['existing_memory']['subjects'],[person])
            return NS(completion_text=json.dumps({'summary':'群中12345678表示目前喜欢咖啡，原始完整capture缺失，仅依现存片段。',
                'fact_operations':[{'op':'create','scope':scope,'subject':person,'name':'饮品','expected_version':None,'value':'咖啡','kind':'stable','importance':.8},
                                   {'op':'create','scope':scope,'subject':'99999999','name':'饮品','expected_version':None,'value':'咖啡'}],
                # Witnesses cannot turn a fragment into the legacy quote writer's source.
                'facts':[{'event':'0','name':'旧式伪字段','value':'咖啡','quote':'我现在喜欢喝咖啡'}],
                'evidence':[{'event':'0','who':'user','quote':'我现在喜欢喝咖啡'}]}))
        self.r.binding=({'memory':True,'memorySummary':True,'naturalConversation':True,'memoryAutoCapture':True},NS(text_chat=chat),lambda:True,self.metrics,contextvars.ContextVar('all_missing',default=None))
        self.assertTrue(self.r.retry());await self.r.task
        self.assertIsNotNone(self.checkpoint());self.assertEqual(len(self.calls),1)
        self.assertEqual([(row[0],row[1]) for row in fact_rows(self.db,scope,person)],[('饮品','咖啡')])
        self.assertEqual(self.db.execute('SELECT subject FROM memory_summary_people').fetchall(),[(person,)])
        self.assertEqual(len(summary_rows(self.db,'private:'+person,person)),1)
        self.assertEqual(summary_rows(self.db,'private:99999999','99999999'),[])
        self.assertEqual(self.db.execute("SELECT subject,decision FROM memory_operation_audit_details WHERE category='facts' ORDER BY position").fetchall(),[(person,'accepted'),('','rejected')])
        self.assertIsNone(self.db.execute('SELECT claims FROM memory_summaries').fetchone()[0])
        self.assertEqual(self.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone()[0],35)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0],35)

    async def test_missing_capture_with_conflicting_protocol_author_cannot_commit(self):
        import message_provenance
        self.record(35)
        self.db.execute('DELETE FROM memory_round_material')
        message_provenance.put(self.db,SCOPE,'0','incoming',{'sender_id':'87654321'})
        self.db.commit();await self.run_summary()
        self.assertEqual(self.calls,[]);self.assertIsNone(self.checkpoint())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)

    def test_author_witness_requires_unambiguous_scope_bound_sender(self):
        from memory_reflection import summary_author_witnesses
        shared=json.dumps(['group:55555','event'])
        witness=summary_author_witnesses('bot_shared',[],[{'event':shared,'source_scope':'group:55555','sender_ids':['12345678']}])
        self.assertEqual(witness,[{'event':shared,'sender_id':'12345678','source':{'sender_id':'12345678','source_scope':'group:55555','event_id':'event'}}])
        self.assertFalse(any(key in witness[0] for key in ('user','bot','text','complete')))
        for change in ({'source_scope':'group:66666'},{'sender_ids':['12345678','87654321']}):
            with self.assertRaisesRegex(ValueError,'author_conflict'):
                summary_author_witnesses('bot_shared',[],[{'event':shared,'source_scope':'group:55555','sender_ids':['12345678'],**change}])

    def test_database_read_error_propagates_instead_of_claiming_missing(self):
        class Broken:
            def execute(self,*args):raise RuntimeError('temporary read failure')
        with self.assertRaisesRegex(RuntimeError,'temporary read failure'):
            semantic_collection.collect_local(Broken(),SCOPE,[])


if __name__=='__main__':unittest.main()
