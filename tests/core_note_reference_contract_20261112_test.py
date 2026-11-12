"""Core-note evidence has one offered namespace; never normalize an ID to accept it."""
import json,sys,unittest
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
                payload=[{'event':event,'sender_id':'12345678'}]
                prior=reflection.core_note()
                for evidence in ('420',420):
                    data={'core_note':{'op':'create','text':'合成交流中更愿意先听完再提建议。','events':[evidence]}}
                    audit=Audit('bot_shared',1,'summary',data)
                    self.assertEqual(reflection.update_core_note(data,prior,payload,[],'bot_shared',audit),0)
                    self.assertEqual(audit.details[('core_note',0)]['reason'],'unknown_event')
                    self.assertEqual(reflection.core_note(),prior)
                data={'core_note':{'op':'create','text':'合成交流中更愿意先听完再提建议。','events':[event]}}
                self.assertEqual(reflection.update_core_note(data,prior,payload,[],'bot_shared'),1)
                self.assertEqual(reflection.core_note()['text'],data['core_note']['text'])
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
