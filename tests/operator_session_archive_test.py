"""Explicit operator session closure; offline, no historical rewrite/replay."""
import hashlib,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'references/astrbot'),str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
from behavior import BehaviorStore
from readonly_journal import ReadonlyJournal
from context_protocol import SOURCE,READONLY_CURRENT,model_json
from journal_cutover import archive_authorized_scope

class OperatorSession(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.addCleanup(self.store.close);self.db=self.store.db
  self.scope='group:963105233';self.key=json.dumps(self.scope)
  self.messages=[{'role':'system','content':'fixed persona'},{'role':'user','content':SOURCE+model_json({'scope':self.scope,'sender_id':'12345678','event_id':'old'})+'\nreal old input'}, {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}]
  journal=ReadonlyJournal(self.db,self.scope);candidate=journal.prepare(self.messages,'old');self.assertTrue(journal.commit(candidate))
  self.original=self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals WHERE scope=?',(self.key,)).fetchone()
  self.store.reflection.record(self.scope,'12345678','old','real old input','confirmed reply')
  self.db.execute("INSERT INTO memory_hold_rounds VALUES(?,?,1,'uncovered',?)",(self.scope,'old',time.time()))
  self.db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created) VALUES(?,35,35,0,'consumed',?)",(self.scope,time.time()))
  self.db.execute("INSERT INTO pipeline_actions VALUES('unknown-operation','fingerprint',0,?,0,'unknown','{}',?)",(self.scope,time.time()));self.db.commit()
 def close_session(self):return archive_authorized_scope(self.db,self.scope,self.original[1],'explicit-user-grant-test','a'*40)
 def test_no_authorization_or_wrong_hash_changes_nothing(self):
  for sha,auth in [(self.original[1],''),('f'*64,'explicit-user-grant-test')]:
   with self.assertRaises(ValueError):archive_authorized_scope(self.db,self.scope,sha,auth,'a'*40)
   self.assertEqual(self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals WHERE scope=?',(self.key,)).fetchone(),self.original)
 def test_archive_preserves_exact_wire_evidence_and_consumed_permissions(self):
  self.close_session()
  encoded,sha=self.db.execute('SELECT archive,sha FROM fishbot_operator_session_archives').fetchone()
  self.assertEqual(hashlib.sha256(encoded.encode()).hexdigest(),sha)
  archive=json.loads(encoded);self.assertEqual(archive['journal']['payload'],self.original[0])
  self.assertEqual(len(archive['evidence']['memory_rounds']['rows']),1)
  self.assertEqual(len(archive['evidence']['memory_hold_rounds']['rows']),1)
  self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
  self.assertEqual(self.db.execute('SELECT state FROM pipeline_actions').fetchone()[0],'unknown')
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
  with self.assertRaises(sqlite3.IntegrityError):self.db.execute("UPDATE fishbot_operator_session_archives SET archive='rewritten'")
  self.db.rollback()
 def test_first_stage_consumes_only_new_session_grant_and_survives_restart(self):
  self.close_session();journal=ReadonlyJournal(self.db,self.scope);self.assertFalse(journal.recovery_error)
  fresh=[self.messages[0],{'role':'user','content':SOURCE+model_json({'scope':self.scope,'sender_id':'12345678','event_id':'new'})+'\nreal new input'},self.messages[-1]]
  candidate=journal.prepare(fresh,'new');self.assertIsNone(candidate.reconstruction_permit)
  self.assertTrue(journal.stage_candidate(candidate))
  self.assertEqual(self.db.execute('SELECT new_session_state FROM fishbot_operator_session_archives').fetchone()[0],'consumed')
  self.assertEqual(ReadonlyJournal(self.db,self.scope).last.wire,candidate.wire)
  self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
 def test_other_scope_stays_unavailable_without_its_own_authorization(self):
  self.close_session();scope='private:12345678'
  self.store.reflection.record(scope,'12345678','private-old','private input','private answer')
  self.assertTrue(ReadonlyJournal(self.db,scope).recovery_error)

if __name__=='__main__':unittest.main()
