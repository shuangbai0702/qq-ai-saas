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
 def test_agreement_user_edit_is_cas_local_and_cannot_forge_linked_work_completion(self):
  import agreement_state
  row,error=agreement_state.apply_update(self.db,'propose','明天见面','12345678',['12345678','bot'],self.scope,'original-event')
  self.assertFalse(error);self.db.commit()
  before=self.snapshot(['facts','relations','memory_core_notes','memory_summaries','memory_rounds','pipeline_events'])
  body={'action':'edit_agreement','actorId':'real-user','id':row['id'],'expectedRevision':1,'topic':'明天见面','detail':'时间待确认','due':'','status':'uncertain'}
  self.assertEqual(run(self.path,{**body,'actorId':''},self.db)['status'],403)
  self.assertTrue(run(self.path,body,self.db)['ok']);self.assertEqual(run(self.path,body,self.db)['status'],409)
  self.assertEqual(self.snapshot(list(before)),before)
  self.db.execute("UPDATE memory_agreements SET handoff_id='real-work' WHERE id=?",(row['id'],));self.db.commit()
  self.assertEqual(run(self.path,{**body,'expectedRevision':2,'status':'fulfilled'},self.db)['status'],409)
  self.assertEqual(agreement_state.review_view(self.db)[0]['status'],'uncertain')
  self.db.execute('PRAGMA query_only=ON');agreement_state.user_records(self.db);agreement_state.review_view(self.db);self.db.execute('PRAGMA query_only=OFF')

 def test_expression_content_edit_keeps_evidence_blocks_and_other_products(self):
  listed=run(self.path,{'action':'list'},self.db);row=listed['patterns'][0]
  data={'action':'edit_pattern','scope':self.scope,'expression':'好耶','expectedVersion':row['version'],'expectedEpoch':listed['epoch'],'situation':'朋友分享红茶时','weight':.3,'actorId':'user'}
  before=self.snapshot(['facts','relations','memory_core_notes','memory_summaries','memory_rounds','companion_interests'])
  self.assertEqual(run(self.path,{**data,'actorId':''},self.db)['status'],403)
  self.assertTrue(run(self.path,data,self.db)['ok']);self.assertEqual(run(self.path,data,self.db)['status'],409)
  row=self.db.execute('SELECT expression,situation,sources,weight FROM social_patterns').fetchone()
  self.assertEqual(row,('好耶','朋友分享红茶时',json.dumps(['e']),.3));self.assertEqual(self.snapshot(list(before)),before)

 def test_emotion_user_correction_is_temporary_local_and_fences_late_summary(self):
  import emotion_state
  self.db.execute('INSERT INTO companion_emotion VALUES(?,?,?,?,?,0)',(self.scope,'angry',2,self.now,self.now+1200))
  self.db.execute('INSERT INTO companion_mood VALUES(?,?,?,?,?)',(self.scope,-.3,.6,self.now,'original'))
  self.db.commit();old=tuple(self.db.execute('SELECT kind,intensity,updated,expires,neutral FROM companion_emotion').fetchone())
  row=run(self.path,{'action':'list'},self.db)['companion']['companion_emotion'][0]
  body={'action':'edit_emotion','actorId':'user','scope':self.scope,'kind':'happy','intensity':2,'expires':self.now+300,'expectedVersion':row['version']}
  other=self.snapshot(['facts','relations','memory_core_notes','memory_summaries','memory_rounds','companion_signals'])
  self.assertEqual(run(self.path,{**body,'actorId':''},self.db)['status'],403)
  self.assertEqual(run(self.path,{**body,'expires':self.now+86400},self.db)['status'],400)
  self.assertTrue(run(self.path,body,self.db)['ok']);self.assertEqual(run(self.path,body,self.db)['status'],409)
  self.assertFalse(emotion_state.apply_summary(self.db,self.scope,{'kind':'angry','intensity':3},{'event':'old'},old,self.now+1))
  self.assertEqual(emotion_state.context(self.db,self.scope,self.now)['kind'],'happy')
  self.assertEqual(emotion_state.context(self.db,self.scope,self.now+301)['kind'],'calm')
  self.assertEqual(self.snapshot(list(other)),other)
  self.db.execute('PRAGMA query_only=ON');emotion_state.state(self.db,self.scope);emotion_state.context(self.db,self.scope,self.now);self.db.execute('PRAGMA query_only=OFF')

 def test_agreement_archive_pagination_has_matching_totals_and_no_overlap(self):
  import agreement_state
  for index in range(105):
   row,error=agreement_state.apply_update(self.db,'propose','旧约定'+str(index),'12345678',['12345678','bot'],self.scope,str(index))
   self.assertFalse(error)
   self.db.execute("UPDATE memory_agreements SET status='canceled' WHERE id=?",(row['id'],))
  self.db.commit()
  first=run(self.path,{'action':'list','subject':'12345678'},self.db)
  second=run(self.path,{'action':'list','subject':'12345678','page':1},self.db)
  self.assertEqual(first['pagination']['totals']['agreements'],105)
  self.assertEqual(first['pagination']['pages'],2)
  self.assertEqual(len(first['agreements']),100);self.assertEqual(len(second['agreements']),5)
  self.assertFalse({r['id'] for r in first['agreements']} & {r['id'] for r in second['agreements']})
  self.assertEqual(agreement_state.user_record_count(self.db,subject='87654321'),0)

 def test_relation_reset_cleans_only_this_person_across_source_scopes(self):
  self.db.execute("INSERT INTO relations(scope,subject,affinity,last_seen) VALUES(?,?,?,?)",(self.scope,'12345678',5,10))
  self.db.execute("INSERT INTO relations(scope,subject,affinity,last_seen) VALUES(?,?,?,?)",(self.scope,'87654321',9,11))
  for scope in (self.scope,'group:23456789'):
   for person in ('12345678','87654321'):
    self.db.execute('INSERT INTO relation_progress VALUES(?,?,?,?,?)',(scope,person,1,1,1))
    self.db.execute('INSERT INTO relation_evidence VALUES(?,?,?,?,?,?,?)',(scope,person,'e',1,'reason','quote',1))
  self.db.commit()
  before=self.snapshot(['facts','memory_core_notes','memory_summaries','memory_rounds','autonomy_decisions'])
  result=run(self.path,{'action':'reset_relation','scope':self.scope,'subject':'12345678','expectedLastSeen':10,'expectedVersion':next(r['version'] for r in run(self.path,{'action':'list'},self.db)['relations'] if r['subject']=='12345678')},self.db)
  self.assertTrue(result['ok']);self.assertEqual(self.snapshot(list(before)),before)
  for table in ('relations','relation_progress','relation_evidence'):
   self.assertFalse(self.db.execute('SELECT 1 FROM '+table+' WHERE subject=?',('12345678',)).fetchone())
   self.assertTrue(self.db.execute('SELECT 1 FROM '+table+' WHERE subject=?',('87654321',)).fetchone())

 def test_user_relation_edit_is_local_and_fences_stale_or_forged_edits(self):
  self.db.execute("INSERT INTO relations(scope,subject,affinity,last_seen,trust,impression,semantic_updated) VALUES(?,?,5,10,.2,'old',1)",(self.scope,'12345678'));self.db.commit()
  row=run(self.path,{'action':'list'},self.db)['relations'][0]
  data={'action':'edit_relation','scope':self.scope,'subject':'12345678','expectedVersion':row['version'],'actorId':'web-user','affinity':30,'trust':.8,'impression':'user correction'}
  before=self.snapshot(['facts','memory_core_notes','memory_summaries','memory_rounds','autonomy_decisions'])
  from relationships import observe_message
  observe_message(self.db,self.scope,'12345678',100);self.db.commit()
  self.assertEqual(run(self.path,{**data,'actorId':''},self.db)['status'],403)
  self.assertTrue(run(self.path,data,self.db)['ok']);self.assertEqual(self.snapshot(list(before)),before)
  row=self.db.execute('SELECT affinity,trust,impression,last_seen,last_message_at FROM relations').fetchone();self.assertEqual(row,(30,.8,'user correction',10,100))
  self.assertEqual(run(self.path,data,self.db)['status'],409)
  self.assertEqual(run(self.path,{'action':'reset_relation','scope':self.scope,'subject':'12345678','expectedLastSeen':10,'expectedVersion':data['expectedVersion']},self.db)['status'],409)

 def test_explicit_interest_and_motive_edits_preserve_sources_and_other_products(self):
  self.db.execute("INSERT INTO companion_interests VALUES(?,?,1,3,1,'original','conversation_heat')",(self.scope,'红茶'))
  self.db.execute("INSERT INTO companion_motives VALUES(?,?,?,1,100,'open','original')",(self.scope,'12345678','喝茶'))
  self.db.commit();before=self.snapshot(['facts','relations','memory_core_notes','memory_summaries','memory_rounds','social_patterns'])
  listed=run(self.path,{'action':'list'},self.db)['companion']
  row=listed['companion_interests'][0];interest={'action':'edit_companion','kind':'interest','scope':self.scope,'topic':'红茶','expectedVersion':row['version'],'weight':.2,'actorId':'user'}
  self.assertTrue(run(self.path,interest,self.db)['ok']);self.assertEqual(run(self.path,interest,self.db)['status'],409)
  self.assertEqual(self.db.execute('SELECT hits,evidence,source FROM companion_interests').fetchone(),(3,'original','conversation_heat'))
  row=listed['companion_motives'][0];motive={'action':'edit_companion','kind':'motive','scope':self.scope,'subject':'12345678','topic':'喝茶','expectedVersion':row['version'],'status':'resolved','due':1,'expires':100,'actorId':'user'}
  self.assertEqual(run(self.path,{**motive,'expires':0},self.db)['status'],400)
  self.assertTrue(run(self.path,motive,self.db)['ok']);self.assertEqual(self.db.execute('SELECT status,evidence FROM companion_motives').fetchone(),('resolved','original'))
  self.assertEqual(self.snapshot(list(before)),before)

if __name__=='__main__':unittest.main()
