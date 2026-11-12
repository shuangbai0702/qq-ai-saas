"""Checkpoint continuity is fixed reference data, never a new source event."""
import asyncio,copy,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from context_foundation import FOUNDATION_CURRENT
from context_protocol import SOURCE,model_json
from readonly_journal import ReadonlyJournal
from context_foundation_20261109_test import messages
from summary_material import snapshots,model_view

from summary_stream_fixture import summary_provider

SCOPE='private:12345678'
def continuity(checkpoint=1):
    return {'checkpoint_id':checkpoint,'items':[{'source_scope':SCOPE,'text':'连续性版本'+str(checkpoint)}],
            'meaning':'检查点连续性资料，不是新发言或授权。'}

def state(checkpoint=1):
    return {'memory_foundation':{'conversation_summaries':[],
        'conversation_continuity':continuity(checkpoint),'facts':[]},'state':{}}

def source_message(event='1'):
    return {'role':'user','content':SOURCE+model_json({'scope':SCOPE,'sender_id':'12345678','event_id':event})+'\n真实原文'+event}

class ContinuityWire(unittest.TestCase):
    def test_single_separate_region_and_no_material_feedback(self):
        journal=ReadonlyJournal()
        raw=[{'role':'system','content':'fixed'},source_message(),{'role':'assistant','content':'确认答复'}]
        prepared=journal.prepare(messages(raw,state()),'stamp')
        regions=[json.loads(m['content'].split('\n',1)[1]) for m in prepared.wire
            if isinstance(m.get('content'),str) and m['content'].startswith(FOUNDATION_CURRENT+'\n')]
        self.assertEqual([r['region'] for r in regions],['conversation_continuity'])
        self.assertEqual(model_json(prepared.wire).count('连续性版本1'),1)
        materials=snapshots(prepared.wire,SCOPE)
        self.assertEqual([m['event'] for m in materials],['1'])
        summarized=model_json(model_view({m['event']:m for m in materials},[]))
        self.assertNotIn('连续性版本',summarized)
        self.assertIn('真实原文',summarized);self.assertIn('确认答复',summarized)

    def test_frozen_until_checkpoint_and_recovered_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'journal.sqlite';db=sqlite3.connect(path)
            journal=ReadonlyJournal(db,SCOPE)
            raw=[{'role':'system','content':'fixed'},source_message()]
            first=journal.prepare(messages(raw,state()),'stamp',now=1)
            self.assertTrue(journal.commit(first));prefix=copy.deepcopy(first.wire);db.close()
            db=sqlite3.connect(path);journal=ReadonlyJournal(db,SCOPE)
            newer=journal.prepare(messages(raw+[{'role':'assistant','content':'reply'},source_message('2')],state(2)),
                'stamp',now=10**10)
            self.assertEqual(newer.wire[:len(prefix)],prefix)
            self.assertEqual(newer.state['memory_foundation']['conversation_continuity'],continuity())
            self.assertNotIn('连续性版本2',model_json(newer.wire))
            self.assertTrue(journal.commit(newer));db.close()
            db=sqlite3.connect(path)
            restored=ReadonlyJournal(db,SCOPE)
            self.assertEqual(restored.last.state['memory_foundation']['conversation_continuity'],continuity())
            db.close()

    def test_product_retirement_runs_inside_same_batch_commit_and_rolls_back(self):
        from semantic_journal_retirement_test import SemanticJournalTests
        fixture=SemanticJournalTests();fixture.setUp()
        try:
            db=fixture.db
            db.execute('CREATE TABLE memory_semantic_batches(checkpoint_id PRIMARY KEY,scope,digest,members,state,completed)')
            db.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?,?,?,NULL)',
                (1,'bot_shared',fixture.permit['batchDigest'],model_json(fixture.permit['batchMembers']),'committed'))
            db.commit();before=db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0]
            def fail(db,scope,checkpoint):
                self.assertTrue(db.in_transaction)
                self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'retired')
                self.assertNotEqual(db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],before)
                raise RuntimeError('retire continuity failed')
            with patch('conversation_continuity.retire_previous',side_effect=fail),patch('conversation_continuity.view',return_value=continuity()):
                with self.assertRaisesRegex(RuntimeError,'retire continuity failed'):
                    fixture.j.retire_semantic_batch(fixture.permit,fixture.authority,state())
            self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'committed')
            self.assertEqual(db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],before)
        finally:fixture.tearDown()

    def test_wrong_checkpoint_product_cannot_retire_previous_or_replace_snapshot(self):
        from semantic_journal_retirement_test import SemanticJournalTests
        from readonly_journal import ContextPrefixViolation
        fixture=SemanticJournalTests();fixture.setUp()
        try:
            db=fixture.db
            db.execute('CREATE TABLE memory_semantic_batches(checkpoint_id PRIMARY KEY,scope,digest,members,state,completed)')
            db.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?,?,?,NULL)',
                (1,'bot_shared',fixture.permit['batchDigest'],model_json(fixture.permit['batchMembers']),'committed'))
            db.commit();before=db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0]
            with patch('conversation_continuity.view',return_value=continuity(2)) as view,patch('conversation_continuity.retire_previous') as retire:
                with self.assertRaisesRegex(ContextPrefixViolation,'semantic_continuity_checkpoint_mismatch'):
                    fixture.j.retire_semantic_batch(fixture.permit,fixture.authority,state())
            view.assert_called_once_with(db,'bot_shared',checkpoint_id=1);retire.assert_not_called()
            self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone()[0],'committed')
            self.assertEqual(db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],before)
        finally:fixture.tearDown()

    def test_fresh_checkpoint_replaces_or_clears_frozen_product(self):
        from semantic_runtime_test import RuntimeTests
        from semantic_runtime import _fresh_state
        fixture=RuntimeTests();fixture.setUp()
        try:
            journal=fixture.st.model_journals[SCOPE]
            journal.last.state['memory_foundation']=copy.deepcopy(state()['memory_foundation'])
            for product in (continuity(2),None):
                def current(db,scope,checkpoint_id=None):
                    self.assertEqual(scope,SCOPE)
                    return product if checkpoint_id is not None else continuity()
                with patch('conversation_continuity.view',side_effect=current) as view:
                    fresh=_fresh_state(fixture.st,{},SCOPE,1,journal)
                view.assert_any_call(fixture.st.db,SCOPE,checkpoint_id=1)
                self.assertEqual(fresh['memory_foundation'].get('conversation_continuity'),product)
                self.assertEqual(journal.last.state['memory_foundation']['conversation_continuity'],continuity())
        finally:fixture.tearDown()

class ContinuityBigMerge(unittest.IsolatedAsyncioTestCase):
    async def test_summary_input_and_foundation_preserve_both_speakers_and_address_target(self):
        from types import SimpleNamespace as NS
        from summary_checkpoint_foundation_test import CheckpointFoundation
        from semantic_runtime import finish_semantic_batches
        fixture=CheckpointFoundation();fixture.setUp();captured=[]
        user='导航先关掉，我叫你长椅同学，陪我坐一会儿。'
        bot='好，我这张长椅陪你歇着，不催你出发。'
        product='用户称机器人“长椅同学”，要求陪坐；机器人自称“这张长椅”，回应陪用户歇着、不催出发。'
        async def chat(**kw):
            captured.append(kw)
            turns=json.loads(kw['prompt'])['turns']
            self.assertEqual(turns[31]['user'],user)
            self.assertEqual(turns[31]['bot'],bot)
            return NS(completion_text=json.dumps({'summary':'独立长期资料',
                'conversation_continuity':[{'source_scope':SCOPE,'text':product}]}))
        try:
            fixture.chat=chat
            journal=ReadonlyJournal(fixture.db,SCOPE)
            initial=journal.prepare(messages([{'role':'system','content':'fixed'},source_message('0')],
                {'memory_foundation':{'conversation_summaries':[]}}),fixture.r.reconstruction_stamp(SCOPE))
            self.assertTrue(journal.commit(initial));fixture.store.model_journals[SCOPE]=journal
            for i in range(35):
                question,answer=(user,bot) if i==31 else ('普通交流'+str(i),'收到')
                msg=source_message(str(i));msg['content']=msg['content'].split('\n',1)[0]+'\n'+question
                fixture.r.record(SCOPE,'12345678',str(i),question,answer,
                    materials=snapshots([msg,{'role':'assistant','content':answer}],SCOPE))
            fixture.r.maybe_start(SCOPE,{'memory':True,'memorySummary':True},summary_provider(chat),lambda:True,{},fixture.var)
            await fixture.r.task
            self.assertEqual(len(captured),1,'both products use the same production summary call')
            self.assertEqual(finish_semantic_batches(fixture.store,{},SCOPE)['retired'],1)
            wire=fixture.store.model_journals[SCOPE].last.wire
            self.assertEqual(model_json(wire).count(product),1)
            self.assertNotIn(product,model_json(snapshots(wire,SCOPE)))
        finally:fixture.tearDown()

    async def test_third_small_waits_for_merge_then_adopts_its_own_continuity(self):
        from types import SimpleNamespace as NS
        from summary_checkpoint_foundation_test import CheckpointFoundation
        from semantic_runtime import finish_semantic_batches
        from conversation_continuity import view
        fixture=CheckpointFoundation();fixture.setUp();running=None
        requests=[];small_count=0
        async def chat(**kw):
            nonlocal small_count
            requests.append(copy.deepcopy(kw))
            if '最多1500' in kw['system_prompt']:
                fixture.merge_entered.set();await fixture.merge_release.wait()
                return NS(completion_text=json.dumps({'summary':'合并后的长期认知',
                    'conversation_continuity':[{'source_scope':SCOPE,'text':'合并模型不能重写的连续性'}]}))
            small_count+=1
            return NS(completion_text=json.dumps({'summary':'完整长期批次'+str(small_count),
                'conversation_continuity':[{'source_scope':SCOPE,'text':'短期交流桥'+str(small_count)}]}))
        fixture.chat=chat
        try:
            journal=ReadonlyJournal(fixture.db,SCOPE)
            raw=[{'role':'system','content':'fixed'},source_message('0')]
            initial=journal.prepare(messages(raw,{'memory_foundation':{'conversation_summaries':[],'facts':[]}}),
                fixture.r.reconstruction_stamp(SCOPE))
            self.assertTrue(journal.commit(initial));fixture.store.model_journals[SCOPE]=journal
            for start in (0,35):
                await fixture.batch(start)
                self.assertEqual(finish_semantic_batches(fixture.store,{},SCOPE)['retired'],1)
            journal=fixture.store.model_journals[SCOPE];old_wire=copy.deepcopy(journal.last.wire)
            old_product=copy.deepcopy(journal.last.state['memory_foundation']['conversation_continuity'])
            self.assertEqual(old_product['items'][0]['text'],'短期交流桥2')
            running=asyncio.create_task(fixture.batch(70));await fixture.merge_entered.wait()
            checkpoint,product,phase=fixture.db.execute(
                'SELECT id,conversation_continuity,state FROM memory_reconstruction_checkpoints ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(phase,'pending_merge');self.assertIn('短期交流桥3',product)
            self.assertEqual(view(fixture.db,SCOPE),old_product)
            self.assertEqual(finish_semantic_batches(fixture.store,{},SCOPE)['retired'],0)
            self.assertEqual(journal.last.wire,old_wire)
            # Both the next raw summary and the big consolidation omit every
            # previous continuity product, even though a real Journal holds it.
            for request in requests[2:]:
                self.assertNotIn('短期交流桥',request['prompt'])
                self.assertNotIn('短期交流桥',model_json(request.get('contexts')))
            fixture.merge_release.set();await running
            self.assertEqual(small_count,3);self.assertEqual(len(requests),4)
            self.assertEqual(fixture.db.execute('SELECT conversation_continuity FROM memory_reconstruction_checkpoints WHERE id=?',(checkpoint,)).fetchone()[0],product)
            self.assertEqual(fixture.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE id=?',(checkpoint,)).fetchone()[0],'available')
            self.assertEqual(finish_semantic_batches(fixture.store,{},SCOPE)['retired'],1)
            fresh=fixture.store.model_journals[SCOPE]
            self.assertEqual(fresh.last.state['memory_foundation']['conversation_continuity']['checkpoint_id'],checkpoint)
            self.assertIn('短期交流桥3',model_json(fresh.last.wire))
            self.assertNotIn('短期交流桥2',model_json(fresh.last.wire))
            self.assertNotIn('合并模型不能重写',model_json(fresh.last.wire))
            self.assertEqual(fixture.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints WHERE id<>? AND conversation_continuity IS NOT NULL',(checkpoint,)).fetchone()[0],0)
            recovered=ReadonlyJournal(fixture.db,SCOPE)
            self.assertEqual(recovered.last.wire,fresh.last.wire)
        finally:
            fixture.merge_release.set()
            if running is not None:await running
            fixture.tearDown()

if __name__=='__main__':unittest.main()
