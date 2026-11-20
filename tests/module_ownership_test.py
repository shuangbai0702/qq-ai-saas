"""Behavioral ownership checks, including SQLite-enforced read-only context."""
import json,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from data_admin import run
import companion_state,person_memory

class Ownership(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite';self.store=BehaviorStore(self.path);self.db=self.store.db;self.now=time.time();self.scope='private:12345678'
  self.store.memory.remember(self.scope,'12345678','饮料','红茶','e',self.now)
  self.db.execute("INSERT INTO social_patterns VALUES(?,?,?,?,?,?)",(self.scope,'好耶','红茶',json.dumps(['e']),1,self.now))
  self.db.execute("INSERT INTO memory_core_notes(slot,text,scopes,people,sources,updated) VALUES(1,'existing','[]','[]','[]',1)")
  self.db.commit()
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def snapshot(self,tables):return {t:self.db.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in tables}
 def test_read_paths_run_with_sqlite_query_only_and_do_not_change_content(self):
  before=self.db.total_changes;self.db.execute('PRAGMA query_only=ON')
  self.assertTrue(self.store.memory.recall(self.scope,'12345678','红茶',self.now))
  self.assertTrue(person_memory.profile(self.db,self.scope,'12345678')['facts'])
  self.store.reflection.core_note();self.store.reflection.foundation('bot_shared')
  companion_state.context(self.db,{},self.scope,self.now,'红茶')
  self.store.social.context(self.scope,'红茶',{'styleLearning':True,'memeEnabled':False})
  self.assertIn('facts',run(self.path,{'action':'list'},self.db))
  self.assertEqual(self.db.total_changes,before);self.db.execute('PRAGMA query_only=OFF')
 def test_fact_lookup_cannot_commit_the_callers_unrelated_transaction(self):
  self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('bot_shared','uncommitted','[]',1)")
  self.store.memory.recall(self.scope,'12345678','红茶',self.now)
  self.assertTrue(self.db.in_transaction);self.db.rollback()
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
 def test_expression_edit_preserves_other_product_content(self):
  tables=['facts','memory_core_notes','memory_summaries','relations','memory_rounds'];before=self.snapshot(tables)
  epoch=self.db.execute('SELECT epoch FROM social_control WHERE id=1').fetchone()[0]
  result=run(self.path,{'action':'set_pattern','scope':self.scope,'expression':'好耶','disabled':True,'expectedUpdated':self.now,'expectedEpoch':epoch},self.db)
  self.assertTrue(result['ok']);self.assertEqual(self.snapshot(tables),before)
  self.assertEqual(self.store.social.expressions(self.scope,'红茶'),[])

 def test_relation_reset_cleans_only_this_person_across_source_scopes(self):
  self.db.execute("INSERT INTO relations(scope,subject,affinity,last_seen) VALUES(?,?,?,?)",(self.scope,'12345678',5,10))
  self.db.execute("INSERT INTO relations(scope,subject,affinity,last_seen) VALUES(?,?,?,?)",(self.scope,'87654321',9,11))
  for scope in (self.scope,'group:23456789'):
   for person in ('12345678','87654321'):
    self.db.execute('INSERT INTO relation_progress VALUES(?,?,?,?,?)',(scope,person,1,1,1))
    self.db.execute('INSERT INTO relation_evidence VALUES(?,?,?,?,?,?,?)',(scope,person,'e',1,'reason','quote',1))
  self.db.commit()
  before=self.snapshot(['facts','memory_core_notes','memory_summaries','memory_rounds','autonomy_decisions'])
  result=run(self.path,{'action':'reset_relation','scope':self.scope,'subject':'12345678','expectedLastSeen':10},self.db)
  self.assertTrue(result['ok']);self.assertEqual(self.snapshot(list(before)),before)
  for table in ('relations','relation_progress','relation_evidence'):
   self.assertFalse(self.db.execute('SELECT 1 FROM '+table+' WHERE subject=?',('12345678',)).fetchone())
   self.assertTrue(self.db.execute('SELECT 1 FROM '+table+' WHERE subject=?',('87654321',)).fetchone())

if __name__=='__main__':unittest.main()
