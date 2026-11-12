"""Reference failures are observable without vetoing valid cognition updates."""
import json,sqlite3,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from memory_operation_audit import Audit
import summary_checkpoint_foundation_test as fixture_module
from readonly_journal import ReadonlyJournal
from context_foundation_20261109_test import messages
from semantic_runtime import finish_semantic_batches
import tempfile


class CoreNoteEvidence(unittest.TestCase):
    def test_shared_encoded_event_is_exact_and_does_not_grant_native_id_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=BehaviorStore(Path(temporary)/'behavior.sqlite')
            try:
                reflection=store.reflection
                event=json.dumps(['private:12345678','420'],ensure_ascii=False,separators=(',',':'))
                payload=[{'event':event,'sender_id':'12345678','source':{'source_scope':'private:12345678'}}]
                for index,evidence in enumerate(('420',420)):
                    prior=reflection.core_note()
                    data={'core_note':{'text':f'交流中更愿意先听完再提建议。{index}','events':[evidence]}}
                    audit=Audit('bot_shared',1,'summary',data)
                    self.assertEqual(reflection.update_core_note(data,prior,payload,[],'bot_shared',audit),1)
                    self.assertEqual(audit.details[('core_note',0)]['reason'],'applied_source_unverified:unknown_event')
                    self.assertEqual(audit.counts['core_note']['accepted'],1)
                    self.assertEqual(reflection.core_note()['source_verification'],'unverified')
                    self.assertEqual(reflection.core_note()['source_scopes'],['private:12345678'])
                    self.assertEqual(reflection.core_note()['subjects'],['12345678'])
                data={'core_note':{'text':'交流中更愿意先听完再提建议。','events':[event]}}
                self.assertEqual(reflection.update_core_note(data,reflection.core_note(),payload,[],'bot_shared'),1)
                self.assertEqual(reflection.core_note()['text'],data['core_note']['text'])
                self.assertEqual(reflection.core_note()['source_verification'],'references_matched')
            finally:store.close()

    def test_missing_malformed_unknown_and_excessive_references_only_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=BehaviorStore(Path(temporary)/'behavior.sqlite')
            try:
                ref=store.reflection;payload=[{'event':'offered','sender_id':'12345678','source':{'source_scope':'private:12345678'}}]
                variants=[{}, {'events':'offered'}, {'summaries':[999999]}, {'summaries':[10**30]}, {'summaries':['1']}, {'events':['offered']*13}, {'events':[{'id':'offered'}]}]
                for index,refs in enumerate(variants):
                    data={'core_note':{'text':f'先听完再建议{index}',**refs}};audit=Audit('bot_shared',1,'summary',data)
                    with store.db:
                        self.assertEqual(ref.update_core_note(data,ref.core_note(),payload,[],'bot_shared',audit),1)
                        audit.persist(store.db,'applied','applied')
                    note=ref.core_note('group:55555','23456789')
                    self.assertEqual(note['source_verification'],'unverified')
                    self.assertEqual(note['source_scopes'],['private:12345678'])
                    self.assertEqual(note['subjects'],['12345678'])
                    self.assertEqual(store.db.execute('SELECT sources FROM memory_core_notes').fetchone()[0],'[]')
                    self.assertEqual(store.db.execute('SELECT decision FROM memory_operation_audit_details WHERE audit_id=?',(audit.id,)).fetchone()[0],'accepted')
                # Retiring source indexes cannot erase the durable uncertainty.
                store.db.execute("UPDATE memory_core_notes SET sources='[]'");store.db.commit()
                self.assertEqual(ref.core_note()['source_verification'],'unverified')
                self.assertEqual(ref.core_note('invalid','23456789')['text'],'')
                with tempfile.TemporaryDirectory() as other:
                    isolated=BehaviorStore(Path(other)/'behavior.sqlite')
                    try:self.assertEqual(isolated.reflection.core_note()['text'],'')
                    finally:isolated.close()
            finally:store.close()

    def test_explicit_foreign_scope_and_snapshot_fences_remain_hard_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=BehaviorStore(Path(temporary)/'behavior.sqlite')
            try:
                ref=store.reflection;payload=[{'event':'offered','sender_id':'12345678','source':{'source_scope':'private:12345678'}}]
                foreign=store.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('private:23456789','foreign','[]',1)").lastrowid
                store.db.commit()
                for refs in ({'events':[json.dumps(['private:23456789','offered'])]}, {'summaries':[foreign]}):
                    data={'core_note':{'text':'不可转移归属',**refs}};audit=Audit('bot_shared',1,'summary',data)
                    self.assertEqual(ref.update_core_note(data,ref.core_note(),payload,[],'bot_shared',audit),0)
                    self.assertEqual(audit.details[('core_note',0)]['reason'],'cross_scope_evidence')
                prior=ref.core_note()
                with store.db:self.assertEqual(ref.update_core_note({'core_note':{'text':'新认知'}},prior,payload,[],'bot_shared'),1)
                self.assertEqual(ref.update_core_note({'core_note':{'text':'迟到认知'}},prior,payload,[],'bot_shared'),0)
                before=ref.core_note()
                with self.assertRaisesRegex(RuntimeError,'transaction failure'):
                    with store.db:
                        self.assertEqual(ref.update_core_note({'core_note':{'text':'必须回滚'}},before,payload,[],'bot_shared'),1)
                        raise RuntimeError('transaction failure')
                self.assertEqual(ref.core_note(),before)
            finally:store.close()

    def test_bot_shared_offered_multiple_scopes_and_summary_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=BehaviorStore(Path(temporary)/'behavior.sqlite')
            try:
                ref=store.reflection;rows=[]
                for origin,person in [('private:12345678','12345678'),('group:55555','23456789')]:
                    ident=store.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,1)',(origin,'提炼经历','[]')).lastrowid
                    store.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(ident,person))
                    rows.append((ident,'提炼经历','[]',1,origin))
                store.db.commit()
                with store.db:self.assertEqual(ref.update_core_note({'core_note':{'text':'综合本批经历'}},ref.core_note(),[],rows,'bot_shared'),1)
                self.assertEqual(ref.core_note()['source_scopes'],['group:55555','private:12345678'])
                self.assertEqual(ref.core_note()['subjects'],['12345678','23456789'])
                self.assertEqual(ref.core_note()['source_verification'],'unverified')
                with store.db:self.assertEqual(ref.update_core_note({'core_note':{'text':'完整引用本批经历','summaries':[r[0] for r in rows]}},ref.core_note(),[],rows,'bot_shared'),1)
                self.assertEqual(ref.core_note()['source_verification'],'references_matched')
            finally:store.close()

    def test_existing_note_migrates_as_unknown_without_rewriting_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'behavior.sqlite'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE memory_core_notes(slot INTEGER PRIMARY KEY CHECK(slot=1),text TEXT NOT NULL,scopes TEXT NOT NULL,people TEXT NOT NULL,sources TEXT NOT NULL,updated REAL NOT NULL)')
            db.execute("INSERT INTO memory_core_notes VALUES(1,'旧认知','[\"private:12345678\"]','[\"12345678\"]','[]',1)")
            db.commit();db.close()
            store=BehaviorStore(path)
            try:
                self.assertEqual(store.reflection.core_note()['text'],'旧认知')
                self.assertEqual(store.reflection.core_note()['source_verification'],'unknown')
                self.assertEqual(store.reflection.core_note()['updated'],1)
            finally:store.close()


class CoreNotePrompt(unittest.IsolatedAsyncioTestCase):
    async def test_real_small_and_big_requests_explain_distinct_evidence_contracts(self):
        fixture=fixture_module.CheckpointFoundation();fixture.setUp()
        try:
            journal=ReadonlyJournal(fixture.db,fixture_module.SCOPE)
            initial=journal.prepare(messages([{'role':'system','content':'fixed'},fixture_module.message('initial')],
                {'memory_foundation':{'conversation_summaries':[]}}),fixture.r.reconstruction_stamp(fixture_module.SCOPE))
            self.assertTrue(journal.commit(initial))
            fixture.store.model_journals[fixture_module.SCOPE]=journal
            calls=[]
            original=fixture.chat
            async def capture(**kw):
                calls.append(kw)
                return await original(**kw)
            fixture.chat=capture
            for start in (0,35,70):
                await fixture.batch(start)
                self.assertEqual(finish_semantic_batches(fixture.store,{},fixture_module.SCOPE)['retired'],1)
            small=[kw for kw in calls if '最多1500' not in kw['system_prompt']]
            big=[kw for kw in calls if '最多1500' in kw['system_prompt']]
            self.assertEqual(len(small),3);self.assertEqual(len(big),1)
            for kw in small:
                self.assertIn('turns中event字段的完整字符串',kw['system_prompt'])
                self.assertIn('不能改用source.event_id',kw['system_prompt'])
                self.assertEqual(len(json.loads(kw['prompt'])['turns']),35)
            self.assertIn('没有可供core_note引用的新聊天event',big[0]['system_prompt'])
            self.assertIn('core_note.events须省略或为空',big[0]['system_prompt'])
            data=json.loads(big[0]['prompt'])
            self.assertTrue(data['summaries'])
            self.assertTrue(all(type(row['id'])is int for row in data['summaries']))
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
