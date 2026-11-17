"""Real reflection/SQLite/SDK HTTP boundary, no paid model or QQ I/O."""
import asyncio,contextvars,copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import compression_catchup_20261113_test as fixture
class Threshold30(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixture.Catchup.asyncSetUp
 asyncTearDown=fixture.Catchup.asyncTearDown
 populate=fixture.Catchup.populate
 def bind(self):
  self.store.reflection.binding=(self.cfg,self.provider,lambda:True,self.metrics,contextvars.ContextVar('threshold30',default=None))
 async def test_29_waits_30_compresses_at_idle(self):
  from memory_policy import DEFAULT_SUMMARY_ROUNDS
  self.assertEqual(DEFAULT_SUMMARY_ROUNDS,30)
  self.populate(29);self.bind();r=self.store.reflection
  self.assertFalse(r.retry());self.assertEqual(self.requests,[])
  # Reuse the authoritative round recorder and append to the same committed Journal.
  from context_protocol import event_message,model_json
  from summary_material import snapshots
  from readonly_journal import Prepared
  import time
  message=event_message({'scope':'private:12345678','sender_id':'12345678','event_id':'29','at':129},'Completed dialogue 29')
  block=[message,{'role':'assistant','content':'Confirmed reply'}]
  r.record('bot_shared','12345678',model_json(['private:12345678','29']),'Completed dialogue 29','Confirmed reply',now=129,materials=snapshots(block,'bot_shared',db=self.store.db))
  journal=self.store.model_journals['bot_shared'];native=copy.deepcopy(journal.last.base)+block;journal.serial+=1
  self.assertTrue(journal.commit(Prepared(journal.serial,None,copy.deepcopy(native),copy.deepcopy(native),{},30,time.time(),'append')))
  self.assertTrue(r.retry());await r.task
  counts=self.store.db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchall()
  self.assertEqual(counts,[(30,)])
  self.assertEqual(len(self.requests),1)
 async def test_failure_retains_history_and_due_responsibility_then_recovers(self):
  self.populate(30);self.bind();r=self.store.reflection;db=self.store.db
  before=db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall()
  wire=copy.deepcopy(self.store.model_journals['bot_shared'].last.wire)
  self.mode='network_error'
  async def no_retry(provider,error,*args,**kwargs):raise error
  with patch.object(type(self.provider),'_handle_api_error',no_retry):
   self.assertTrue(r.retry());await r.task
  self.assertEqual(db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall(),before)
  self.assertEqual(self.store.model_journals['bot_shared'].last.wire,wire)
  self.assertFalse(r.retry()) # Existing bounded backoff, no spin.
  self.mode='stop';db.execute('UPDATE memory_reflection_retry SET next_at=0');db.commit()
  self.assertTrue(r.retry());await r.task
  self.assertEqual(db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchall(),[(30,)])
if __name__=='__main__':unittest.main()
