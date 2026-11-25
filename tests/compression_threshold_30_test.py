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
 async def test_provider_failure_retires_at_real_checkpoint_with_explicit_loss_receipt(self):
  self.populate(30);self.bind();r=self.store.reflection;db=self.store.db
  before=db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall()
  wire=copy.deepcopy(self.store.model_journals['bot_shared'].last.wire)
  self.mode='network_error'
  async def no_retry(provider,error,*args,**kwargs):raise error
  with patch.object(type(self.provider),'_handle_api_error',no_retry):
   self.assertTrue(r.retry());await r.task
  self.assertEqual(db.execute('SELECT * FROM memory_rounds ORDER BY id').fetchall(),[])
  self.assertNotEqual(self.store.model_journals['bot_shared'].last.wire,wire)
  self.assertFalse(r.retry())
  audit=db.execute("SELECT counts FROM memory_operation_audits WHERE reason='degraded_checkpoint'").fetchone()
  self.assertEqual(json.loads(audit[0])['degradation']['rounds'],len(before))
  self.assertEqual(db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchall(),[(30,)])
 async def test_retirement_removes_only_obsolete_copies_and_keeps_current_cognition(self):
  from hosted_memory import retire_legacy_results
  from companion_state import retire_persona_seeds
  db=self.store.db
  for name,origin,updated in [('old-task','autonomous_result',1),('future-task','autonomous_result',9999999999),('current-cognition','explicit',1)]:
   db.execute("INSERT INTO facts(scope,subject,name,value,event,updated,version,active) VALUES('self','0',?,'retained value','source',?,1,1)",(name,updated))
   db.execute("INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES('self','0',?,'event',?,.5)",(name,origin))
  db.execute("INSERT INTO companion_interests VALUES('*','persona-copy',.4,1,1,'source','persona')")
  db.execute("INSERT INTO companion_interests VALUES('self','learned-interest',1,3,1,'source','conversation_heat')");db.commit()
  db.execute("INSERT INTO companion_motives VALUES('private:12345678','12345678','expired-topic',40,50,'open','source')")
  db.execute("INSERT INTO companion_interests VALUES('private:12345678','derived-interest',1,3,1,'0','conversation_heat')")
  old=db.execute("INSERT INTO social_samples(scope,event,subject,text,created) VALUES('private:12345678','0','12345678','旧样本',100)").lastrowid
  fresh=db.execute("INSERT INTO social_samples(scope,event,subject,text,created) VALUES('private:12345678','future','12345678','未退休样本',9999999999)").lastrowid
  db.execute("INSERT INTO social_patterns VALUES('private:12345678','学到的表达','适用语境',?,2,1)",(json.dumps([old,fresh]),));db.commit()
  db.execute('BEGIN IMMEDIATE');self.assertEqual(retire_legacy_results(db,100),1);self.assertEqual(retire_persona_seeds(db,100),1);db.rollback()
  self.assertEqual(db.execute('SELECT count(*) FROM facts').fetchone()[0],3)
  self.populate(30);self.bind();r=self.store.reflection;self.assertTrue(r.retry());await r.task
  self.assertEqual({x[0] for x in db.execute('SELECT name FROM facts')},{'future-task','current-cognition'})
  self.assertEqual({x[0] for x in db.execute('SELECT topic FROM companion_interests')},{'learned-interest','derived-interest'})
  self.assertEqual(json.loads(db.execute('SELECT sources FROM social_patterns').fetchone()[0]),[fresh])
  self.assertTrue(db.execute("SELECT evidence FROM companion_interests WHERE topic='derived-interest'").fetchone()[0].startswith('semantic:'))
  self.assertEqual(db.execute('SELECT count(*) FROM companion_motives').fetchone()[0],0)
  proof=db.execute("SELECT detail FROM memory_checkpoint_issues WHERE kind='legacy_cognitive_copy_cleanup'").fetchone()
  self.assertEqual(json.loads(proof[0]),{'researchTaskCopies':1,'personaInterestCopies':1,'expiredTopics':1})
if __name__=='__main__':unittest.main()
