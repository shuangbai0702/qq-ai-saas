"""D3 correction chain: value-level blocks, evidence-checked application,
no resurrection by late extractions, no downgrade of hard forgets."""
import json,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from companion_insights import apply,snapshot
from data_admin import initialize_controls
from memory_scope import value_hash
import sqlite3

NOW=1000.0


class Correction(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
  self.db=self.store.db;self.scope='private:u1';self.person='u1';self.n=0
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def insight(self,payload,data,before,scope=None):
  self.n+=1
  return apply(self.db,self.store.memory,scope or self.scope,{'memory':True,'memoryAutoCapture':True},payload,data,before,now=NOW+self.n)
 def block_row(self,scope=None,subject=None,name='称呼'):
  return self.db.execute('SELECT block_all,rejected_values,revision FROM memory_blocks WHERE scope=? AND subject=? AND name=?',(scope or self.scope,subject or self.person,name)).fetchone()
 def test_author_correction_writes_new_value_and_value_block(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  saved=self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，以后叫我小寒','bot':'好的','at':NOW}],
   {'summary':'纠正称呼','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，以后叫我小寒'}]},before)
  facts=self.store.observe(self.scope,self.person,'我叫什么','e3',now=NOW+1,auto_capture=True)['facts']
  self.assertEqual(facts[0]['value'],'小寒');self.assertEqual(facts[0]['origin'],'explicit')
  block=self.block_row()
  self.assertEqual(block[0],0);self.assertEqual(block[2],1)
  self.assertIn(value_hash('江哥'),[e['v'] for e in json.loads(block[1])])
  self.assertEqual([e['event'] for e in json.loads(block[1])],['e2'])
  self.assertGreater(saved,0)
 def test_late_extraction_and_repeat_never_revive_old_value(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，叫我小寒','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，叫我小寒'}]},before)
  for round in range(2):
   before=snapshot(self.db,self.scope,{self.person})
   saved=self.insight([{'event':'late'+str(round),'sender_id':self.person,'user':'我叫江哥','bot':'嗯','at':NOW+round}],
    {'summary':'迟到的旧抽取','facts':[{'event':'late'+str(round),'name':'称呼','value':'江哥','quote':'我叫江哥','kind':'stable'}]},before)
   self.assertEqual(saved,0,'round '+str(round))
  facts=self.store.observe(self.scope,self.person,'我叫什么','e9',now=NOW+9,auto_capture=True)['facts']
  self.assertEqual(facts[0]['value'],'小寒')
 def test_other_person_same_name_unaffected(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，叫我小寒','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，叫我小寒'}]},before)
  other_scope='private:u2'
  self.store.observe(other_scope,'u2','记住：称呼=江哥','e3',now=NOW+1)
  facts=self.store.observe(other_scope,'u2','我叫什么','e4',now=NOW+2,auto_capture=True)['facts']
  self.assertEqual(facts[0]['value'],'江哥')
  self.assertIsNone(self.block_row(other_scope,'u2'))
 def test_third_person_bot_apology_and_forged_quote_do_not_write(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  saved=self.insight([{'event':'e2','sender_id':self.person,'user':'刚才你说你叫错了名字','bot':'抱歉，我之前把称呼说错了，不叫江哥','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'抱歉，我之前把称呼说错了，不叫江哥'}]},before)
  self.assertEqual(saved,0,'bot apology is not the author correction')
  saved=self.insight([{'event':'e3','sender_id':self.person,'user':'对了顺便提一下','bot':'嗯','at':NOW}],
   {'summary':'c','corrections':[{'event':'e3','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我没说过的话片段'}]},before)
  self.assertEqual(saved,0,'forged quote is not a substring of the real user text')
  saved=self.insight([{'event':'e4','sender_id':self.person,'user':'他不叫江哥吧','bot':'嗯','at':NOW}],
   {'summary':'c','corrections':[{'event':'e4','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'他不叫江哥吧'}]},before)
  self.assertEqual(saved,0,'third person pronoun waits for review')
  signals=self.db.execute("SELECT kind FROM companion_signals WHERE kind='correction_unverified'").fetchall()
  self.assertEqual(len(signals),1,'exactly the ambiguous case is parked for verification')
  self.assertEqual(self.store.observe(self.scope,self.person,'我叫什么','e5',now=NOW+5,auto_capture=True)['facts'][0]['value'],'江哥')
 def test_correction_without_new_value_retires_the_fact(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'别再叫我江哥了','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','quote':'别再叫我江哥了'}]},before)
  self.assertEqual(self.store.observe(self.scope,self.person,'我叫什么','e3',now=NOW+1,auto_capture=True)['facts'],[])
  block=self.block_row();self.assertEqual(block[0],0);self.assertEqual(block[2],1)
 def test_hard_forget_row_never_downgrades(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，叫我小寒','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，叫我小寒'}]},before)
  self.store.observe(self.scope,self.person,'忘记：称呼','e3',now=NOW+1)
  self.assertEqual(self.block_row()[0],1)
  revision=self.block_row()[2]
  before=snapshot(self.db,self.scope,{self.person})
  saved=self.insight([{'event':'e4','sender_id':self.person,'user':'我改成小寒了','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e4','name':'称呼','oldValue':'小寒','newValue':'寒哥','quote':'我改成小寒了'}]},before)
  self.assertEqual(saved,0,'no current fact matches behind a whole-name forget')
  self.assertEqual(self.block_row()[0],1);self.assertEqual(self.block_row()[2],revision)
 def test_upsert_merges_rejected_values_and_keeps_block_all(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，叫我小寒','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，叫我小寒'}]},before)
  self.insight([{'event':'e3','sender_id':self.person,'user':'小寒也不对，我叫阿寒','bot':'好','at':NOW}],
   {'summary':'c2','corrections':[{'event':'e3','name':'称呼','oldValue':'小寒','newValue':'阿寒','quote':'小寒也不对，我叫阿寒'}]},snapshot(self.db,self.scope,{self.person}))
  block=self.block_row()
  self.assertEqual(block[0],0);self.assertEqual(block[2],2)
  self.assertEqual([e['v'] for e in json.loads(block[1])],[value_hash('江哥'),value_hash('小寒')])
 def test_explicit_remember_lifts_only_its_own_value(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，叫我小寒','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，叫我小寒'}]},before)
  self.insight([{'event':'e3','sender_id':self.person,'user':'小寒也不对，我叫阿寒','bot':'好','at':NOW}],
   {'summary':'c2','corrections':[{'event':'e3','name':'称呼','oldValue':'小寒','newValue':'阿寒','quote':'小寒也不对，我叫阿寒'}]},snapshot(self.db,self.scope,{self.person}))
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e4',now=NOW+4)
  entries=[e['v'] for e in json.loads(self.block_row()[1])]
  self.assertNotIn(value_hash('江哥'),entries,'the explicitly restored value is unblocked')
  self.assertIn(value_hash('小寒'),entries,'other correction records stay')
  self.assertEqual(self.store.observe(self.scope,self.person,'我叫什么','e5',now=NOW+5,auto_capture=True)['facts'][0]['value'],'江哥')
 def test_context_dependency_tracks_correction_revision(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before_dependency=self.store.reflection.context_dependency([],self.scope)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，叫我小寒','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，叫我小寒'}]},before)
  self.assertNotEqual(self.store.reflection.context_dependency([],self.scope),before_dependency)
  self.assertEqual(self.store.reflection.context_dependency([],'group:55555'),self.store.reflection.context_dependency([],'group:55555'))
 def test_legacy_block_rows_migrate_to_whole_name_forget(self):
  path=Path(self.tmp.name)/'legacy.sqlite'
  db=sqlite3.connect(path)
  db.execute('CREATE TABLE memory_blocks(scope TEXT,subject TEXT,name TEXT,PRIMARY KEY(scope,subject,name))')
  db.execute("INSERT INTO memory_blocks VALUES('private:x','x','称呼')")
  db.commit()
  initialize_controls(db)
  row=db.execute("SELECT block_all,rejected_values,revision FROM memory_blocks WHERE name='称呼'").fetchone()
  self.assertEqual(row[0],1,'a pre-existing forget row stays a whole-name forget')
  db.close()
 def test_correction_value_blocked_for_model_renamed_field(self):
  self.store.observe(self.scope,self.person,'记住：称呼=江哥','e1',now=NOW)
  before=snapshot(self.db,self.scope,{self.person})
  self.insight([{'event':'e2','sender_id':self.person,'user':'我不叫江哥，叫我小寒','bot':'好','at':NOW}],
   {'summary':'c','corrections':[{'event':'e2','name':'称呼','oldValue':'江哥','newValue':'小寒','quote':'我不叫江哥，叫我小寒'}]},before)
  before=snapshot(self.db,self.scope,{self.person})
  saved=self.insight([{'event':'e3','sender_id':self.person,'user':'我叫江哥','bot':'嗯','at':NOW}],
   {'summary':'迟到的旧值换个字段名','facts':[{'event':'e3','name':'怎么称呼','value':'江哥','quote':'我叫江哥','kind':'stable'}]},before)
  self.assertEqual(saved,0,'a model-renamed field cannot smuggle a corrected value back')

if __name__=='__main__':unittest.main(verbosity=2)
