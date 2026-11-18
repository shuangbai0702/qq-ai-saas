"""Paid response recovery in the original retry slot and checkpoint transaction."""
import json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parent)]
from semantic_batch_summary_test import SemanticSummary
from memory_reflection import SummaryResponseSlot,SummaryPersistenceError,summary_request_digest,summary_model_output
class Recovery(unittest.IsolatedAsyncioTestCase):
 setUp=SemanticSummary.setUp
 tearDown=SemanticSummary.tearDown
 record=SemanticSummary.record
 chat=SemanticSummary.chat
 run_summary=SemanticSummary.run_summary
 checkpoint=SemanticSummary.checkpoint
 def test_response_digest_binds_sources_model_and_epoch_not_clock(self):
  provider=NS(provider_config={'model':'test','temperature':0})
  first={'prompt':json.dumps({'current_time':1,'turns':['real']}),'system_prompt':'stable'}
  later={**first,'prompt':json.dumps({'current_time':2,'turns':['real']})}
  digest=summary_request_digest(first,provider,1,'summary',{'materialsSha':'a'})
  self.assertEqual(digest,summary_request_digest(later,provider,1,'summary',{'materialsSha':'a'}))
  self.assertNotEqual(digest,summary_request_digest(first,provider,2,'summary',{'materialsSha':'a'}))
  self.assertNotEqual(digest,summary_request_digest(first,provider,1,'summary',{'materialsSha':'b'}))
  provider.provider_config['temperature']=1
  self.assertNotEqual(digest,summary_request_digest(first,provider,1,'summary',{'materialsSha':'a'}))
 async def test_saved_response_survives_failed_commit_and_is_not_rebought(self):
  self.record();self.db.execute("CREATE TRIGGER reject_summary BEFORE INSERT ON memory_summaries BEGIN SELECT RAISE(ABORT,'injected crash boundary'); END");self.db.commit()
  await self.run_summary()
  self.assertEqual(len(self.calls),1);self.assertIsNone(self.checkpoint())
  row=self.db.execute('SELECT result FROM memory_reflection_retry WHERE scope=?',(self.scope if hasattr(self,'scope') else 'private:12345678',)).fetchone()
  self.assertEqual(json.loads(row[0])['phase'],'response_saved')
  # Reopen the real SQLite connection and original Reflection before retrying.
  from behavior import BehaviorStore
  self.store.close();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.r=self.store.reflection;self.db=self.store.db
  self.r.shared_context=False  # Reopen the same deliberately local reflection fixture.
  self.r.binding=None;self.db.execute('DROP TRIGGER reject_summary');self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
  await self.run_summary()
  self.assertIsNotNone(self.checkpoint());self.assertEqual(len(self.calls),1)
  self.assertEqual(self.metrics['summaryResponsesRecovered'],1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reflection_retry').fetchone()[0],0)
 async def test_old_epoch_or_attempt_cannot_write_new_owner(self):
  epoch=self.r.epoch();self.db.execute('INSERT INTO memory_reflection_retry(scope,attempts,next_at) VALUES(?,2,0)',('private:12345678',));self.db.commit()
  slot=SummaryResponseSlot(self.db,'private:12345678',1,epoch,'old')
  response=NS(completion_text='{"summary":"done"}',raw_completion={'choices':[{'finish_reason':'stop'}]},is_chunk=False)
  with self.assertRaises(SummaryPersistenceError):slot.save(response)
  current=SummaryResponseSlot(self.db,'private:12345678',2,epoch,'new');current.dispatch();current.save(response)
  self.assertIsNone(slot.recover());self.assertIsNotNone(current.recover())
  self.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1');self.db.commit()
  current.save(NS(completion_text='late',is_chunk=False))
  self.assertEqual(current.recover().completion_text,'{"summary":"done"}')
 async def test_partial_response_never_becomes_recoverable(self):
  epoch=self.r.epoch();self.db.execute('INSERT INTO memory_reflection_retry(scope,attempts,next_at) VALUES(?,1,0)',('private:12345678',));self.db.commit()
  slot=SummaryResponseSlot(self.db,'private:12345678',1,epoch,'digest');slot.dispatch()
  with self.assertRaises(SummaryPersistenceError):slot.save(NS(completion_text='partial',is_chunk=True))
  self.assertIsNone(slot.recover())
 async def test_final_is_saved_before_transport_close_failure_without_repurchase(self):
  epoch=self.r.epoch();self.db.execute('INSERT INTO memory_reflection_retry(scope,attempts,next_at) VALUES(?,1,0)',('private:12345678',));self.db.commit()
  slot=SummaryResponseSlot(self.db,'private:12345678',1,epoch,'digest');slot.dispatch()
  calls=[]
  async def stream(**kwargs):
   calls.append(1)
   yield NS(completion_text='{"summary":"complete"}',raw_completion={'choices':[{'finish_reason':'stop'}]},is_chunk=False)
   self.assertIsNotNone(slot.recover(),'final must be durable before transport cleanup')
   raise RuntimeError('late stream close failure')
  metrics={};result=await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,metrics,800,on_response=slot.save)
  self.assertEqual(len(calls),1);self.assertEqual(result[2],'complete')
  self.assertEqual(metrics['summaryStreamErrorsAfterFinal'],1)
del SemanticSummary
if __name__=='__main__':unittest.main()
