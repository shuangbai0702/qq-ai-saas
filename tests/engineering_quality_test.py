"""Cross-owner regression with real SQLite; no model or production access."""
import hashlib,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from companion_insights import apply,apply_semantic_memory,snapshot
from data_admin import run
from memory_operation_audit import Audit

class Quality(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
  self.store=BehaviorStore(self.path);self.db=self.store.db;self.scope='private:12345';self.now=time.time()
  self.payload=[{'event':'e','sender_id':'12345','user':'我们下次聊红茶','bot':'下次继续聊红茶，我喜欢红茶'}]
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def test_admin_refuses_borrowed_transaction_without_committing_or_rolling_it_back(self):
  self.db.execute("INSERT INTO companion_interests VALUES(?,?,1,1,1,'e','bot_expression')",(self.scope,'红茶'))
  for body in ({'action':'list'},{'action':'clear_learning','scope':self.scope,'expectedEpoch':0,'actorId':'user'}):
   self.assertEqual(run(self.path,body,self.db)['status'],409)
   self.assertTrue(self.db.in_transaction)
   self.assertEqual(self.db.execute('SELECT count(*) FROM companion_interests').fetchone()[0],1)
  self.db.rollback();self.assertEqual(self.db.execute('SELECT count(*) FROM companion_interests').fetchone()[0],0)
 def test_all_expression_user_actions_require_actor_without_mutating_epoch(self):
  self.db.execute("INSERT INTO social_patterns VALUES(?,?,?,'[]',1,1)",(self.scope,'好耶','红茶'));self.db.commit()
  for body in ({'action':'set_pattern','expression':'好耶','disabled':True,'expectedUpdated':1}, {'action':'clear_learning'}):
   self.assertEqual(run(self.path,{**body,'scope':self.scope,'expectedEpoch':0},self.db)['status'],403)
  self.assertEqual(self.db.execute('SELECT epoch FROM social_control').fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM social_patterns').fetchone()[0],1)
 def test_expression_failure_rolls_back_epoch_content_and_proof(self):
  from unittest.mock import patch
  self.db.execute("INSERT INTO social_patterns VALUES(?,?,?,'[]',1,1)",(self.scope,'好耶','红茶'));self.db.commit()
  body={'action':'set_pattern','expression':'好耶','disabled':True,'expectedUpdated':1,'expectedEpoch':0,'scope':self.scope,'actorId':'user'}
  with patch('social._record_user_edit',side_effect=RuntimeError('injected storage failure')):
   with self.assertRaisesRegex(RuntimeError,'injected'):run(self.path,body,self.db)
  self.assertFalse(self.db.in_transaction)
  self.assertEqual(self.db.execute('SELECT epoch FROM social_control').fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM social_pattern_blocks').fetchone()[0],0)
  self.assertTrue(run(self.path,body,self.db)['ok'])
 def test_two_real_sqlite_connections_cannot_both_commit_the_same_revision(self):
  from concurrent.futures import ThreadPoolExecutor
  from threading import Barrier
  self.db.execute("INSERT INTO social_patterns VALUES(?,?,?,'[]',1,1)",(self.scope,'好耶','红茶'));self.db.commit()
  barrier=Barrier(2)
  def edit(disabled):
   barrier.wait(timeout=5)
   return run(self.path,{'action':'set_pattern','expression':'好耶','disabled':disabled,'expectedUpdated':1,'expectedEpoch':0,'scope':self.scope,'actorId':'user'})
  with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(edit,(True,False)))
  self.assertEqual(sum(r.get('ok',False) for r in results),1)
  self.assertEqual([r['status'] for r in results if not r.get('ok')],[409])
  self.assertEqual(self.db.execute('SELECT epoch FROM social_control').fetchone()[0],1)
 def test_explicit_motive_delete_wins_over_legacy_evidence_in_same_summary(self):
  self.db.execute("INSERT INTO companion_motives VALUES(?,?,?,1,9999999999,'open','old')",(self.scope,'12345','红茶'));self.db.commit()
  before=snapshot(self.db,self.scope,{'12345'})
  target=before['memory_state']['products']['motive'][0]
  data={'memory_operations':[{'kind':'motive','op':'delete','target':target['target'],'expected_version':target['version']}],
        'motives':[{'event':'e','topic':'红茶','quote':'下次继续聊红茶','status':'open'}]}
  with self.db:
   apply_semantic_memory(self.db,self.store.memory,self.scope,{},self.payload,data,before,self.now)
   apply(self.db,self.store.memory,self.scope,{},self.payload,data,before,self.now)
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_motives').fetchone()[0],0)
 def test_late_evidence_cannot_change_user_edited_topic(self):
  self.db.execute("INSERT INTO companion_motives VALUES(?,?,?,1,9999999999,'open','old')",(self.scope,'12345','红茶'));self.db.commit()
  before=snapshot(self.db,self.scope,{'12345'})
  self.db.execute("UPDATE companion_motives SET status='abandoned' WHERE topic='红茶'");self.db.commit()
  with self.db:apply(self.db,self.store.memory,self.scope,{},self.payload,{'motives':[{'event':'e','topic':'红茶','quote':'下次继续聊红茶','status':'resolved'}]},before,self.now)
  self.assertEqual(self.db.execute('SELECT status FROM companion_motives').fetchone()[0],'abandoned')
 def test_other_scopes_open_topics_are_not_capacity_evicted(self):
  for i in range(70):self.db.execute("INSERT INTO companion_motives VALUES(?,?,?,1,9999999999,'open','old')",('group:77777','12345',str(i)))
  self.db.execute("INSERT INTO companion_motives VALUES(?,?,?,1,2,'resolved','old')",('group:77777','12345','旧话题'))
  self.db.commit();before=snapshot(self.db,self.scope,{'12345'})
  with self.db:apply(self.db,self.store.memory,self.scope,{},self.payload,{},before,self.now)
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_motives').fetchone()[0],71)
 def test_obsolete_fact_and_relation_payload_cannot_bypass_canonical_owner(self):
  before=snapshot(self.db,self.scope,{'12345'})
  with self.db:apply(self.db,self.store.memory,self.scope,{'memoryAutoCapture':True},self.payload,
    {'facts':[{'event':'e','name':'偏好','value':'红茶','quote':'我喜欢红茶'}],'relations':[{'event':'e','sentiment':1,'quote':'我们下次聊红茶'}]},before,self.now)
  self.assertEqual(self.db.execute('SELECT count(*) FROM facts').fetchone()[0],0)
 def test_forget_removes_only_exact_fact_receipt_and_keeps_other_products(self):
  self.store.memory.remember(self.scope,'12345','饮料','红茶','e',1);self.db.commit()
  data={'fact_operations':[{'op':'create','subject':'12345','name':'饮料'},{'op':'create','subject':'12345','name':'城市'}],
        'relation_operations':[{'op':'replace','subject':'12345'}]}
  with self.db:Audit(self.scope,0,'summary',data,{'12345'}).persist(self.db,'committed','test')
  self.assertTrue(run(self.path,{'action':'forget_fact','actorId':'user','scope':self.scope,'subject':'12345','name':'饮料','expectedVersion':1},self.db)['ok'])
  details=self.db.execute('SELECT category,target_hash FROM memory_operation_audit_details ORDER BY category').fetchall()
  self.assertEqual(details,[('facts',hashlib.sha256('城市'.encode()).hexdigest()),('relations','')])

 def test_correction_does_not_erase_unrelated_product_receipts(self):
  self.store.memory.remember(self.scope,'12345','饮料','红茶','e',1);self.db.commit()
  data={'fact_operations':[{'op':'create','subject':'12345','name':'饮料'},{'op':'create','subject':'12345','name':'城市'}],
        'relation_operations':[{'op':'replace','subject':'12345'}]}
  with self.db:
   Audit(self.scope,0,'summary',data,{'12345'}).persist(self.db,'committed','test')
   self.assertEqual(self.store.memory.correct(self.scope,'12345','饮料','红茶','绿茶','new','我改喝绿茶',self.now),'已按本人证据纠正资料')
  self.assertEqual(self.db.execute('SELECT category FROM memory_operation_audit_details ORDER BY category').fetchall(),[('facts',),('relations',)])

 def test_standalone_list_connection_is_sqlite_enforced_read_only(self):
  import sqlite3
  from unittest.mock import patch
  original=sqlite3.connect
  def connect(*args,**kwargs):
   db=original(*args,**kwargs)
   with self.assertRaisesRegex(sqlite3.OperationalError,'readonly'):
    db.execute('CREATE TABLE forbidden_write(id INTEGER)')
   return db
  with patch('data_admin.sqlite3.connect',connect):
   self.assertNotIn('error',run(self.path,{'action':'list'}))
  self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='forbidden_write'").fetchone())

if __name__=='__main__':unittest.main()
