"""Legacy merge cleanup cannot preempt any committed semantic batch owner."""
import contextvars,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from context_protocol import model_json
from summary_material_test import message,SCOPE
import summary_material,semantic_batch


class CrossScopeMergeGuard(unittest.IsolatedAsyncioTestCase):
    async def test_original_scope_merge_preserves_shared_batch_member_and_deletes_only_unowned_legacy(self):
        await self.merge_case(SCOPE,'bot_shared')

    async def test_shared_merge_preserves_original_scope_batch_member(self):
        await self.merge_case('bot_shared',SCOPE)

    async def merge_case(self,merge_scope,batch_scope):
        with tempfile.TemporaryDirectory() as folder:
            store=BehaviorStore(Path(folder)/'behavior.sqlite');db=store.db;r=store.reflection
            try:
                summaries=[db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,'[]',1)",(merge_scope,'已提炼'+str(i))).lastrowid for i in range(3)]
                event_key=lambda event:model_json([SCOPE,event]) if merge_scope=='bot_shared' else event
                for original in ('shared-owned','unowned'):
                    event=event_key(original)
                    material=summary_material.snapshots([message(original),{'role':'assistant','content':'完整真实回复'}],merge_scope)[0]
                    # Legacy v1 published Coverage is enough to exercise this
                    # cleanup; shared v3 snapshots need live delivery owners.
                    material.update(version=1,complete=True,reply='完整真实回复')
                    material.pop('input_settlement',None);material.pop('native_outputs',None)
                    self.assertTrue(summary_material.coverage_eligible(material,merge_scope))
                    db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(merge_scope,event,'12345678',json.dumps(material)))
                    db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(merge_scope,'12345678',event,'看这个','完整真实回复',90))
                    db.execute('INSERT INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)',(merge_scope,'12345678',event,'看这个','完整真实回复',90))
                    db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',(merge_scope,event,'12345678',summary_material.coverage_digest(material),summaries[0]))
                member=semantic_batch.event_key(batch_scope,model_json([SCOPE,'shared-owned']) if batch_scope=='bot_shared' else 'shared-owned')
                db.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?,?,?,?,?,NULL)',(999,batch_scope,'d'*64,model_json([member]),100,'committed',1))
                db.commit()
                originals={table:db.execute('SELECT * FROM '+table+' WHERE scope=? AND event=?',(merge_scope,event_key('shared-owned'))).fetchall() for table in ('memory_rounds','memory_round_material','memory_pending')}
                calls=[]
                async def chat(**kw):calls.append(kw);return NS(completion_text='合并后的持续记忆')
                metrics={};r.binding=({'memory':True,'memorySummary':True},NS(text_chat=chat),lambda:True,metrics,contextvars.ContextVar('cross_merge',default=None))
                self.assertTrue(r.retry());await r.task;r.binding=None
                self.assertEqual(len(calls),1);self.assertEqual(metrics.get('bigSummariesSaved'),1)
                self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone(),('committed',))
                for table,before in originals.items():
                    self.assertEqual(db.execute('SELECT * FROM '+table+' WHERE scope=? AND event=?',(merge_scope,event_key('shared-owned'))).fetchall(),before,table)
                    self.assertIsNone(db.execute('SELECT 1 FROM '+table+' WHERE scope=? AND event=?',(merge_scope,event_key('unowned'))).fetchone(),table)
            finally:store.close()


if __name__=='__main__':unittest.main()
