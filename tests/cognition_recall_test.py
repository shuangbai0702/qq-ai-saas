import sqlite3,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import companion_state
import memory_products
from memory_operation_audit import Audit
from social import SocialStore


class CognitionRecallTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=sqlite3.connect(':memory:')
  companion_state.initialize(self.db);self.social=SocialStore(self.db,Path(self.tmp.name));self.now=time.time()
 def tearDown(self):self.social.close();self.db.close();self.tmp.cleanup()
 def test_persona_read_is_read_only_and_does_not_repeat_legacy_seeds(self):
  self.db.execute('INSERT INTO companion_interests VALUES(?,?,?,?,?,?,?)',('*','的东西',3,4,self.now,'legacy','persona'))
  self.db.execute('INSERT INTO companion_interests VALUES(?,?,?,?,?,?,?)',('group:12345','昆曲',1,3,self.now,'evidence','conversation_heat'))
  self.db.commit();before=self.db.total_changes
  self.db.execute('PRAGMA query_only=ON')
  for persona in ('喜欢的东西、开心激动时话也会变多。','爱好的事话变多。','兴趣：昆曲。'):
   state=companion_state.context(self.db,{'systemPrompt':persona},'group:12345',self.now,'昆曲')
   self.assertEqual([x['topic'] for x in state['interests']],['昆曲'])
  self.assertEqual(self.db.total_changes,before)
  self.assertEqual(self.db.execute("SELECT topic FROM companion_interests WHERE source='persona'").fetchall(),[('的东西',)])
 def test_zero_relevance_is_not_filled_with_unrelated_expression(self):
  self.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',('group:12345','有节目效果','轻松开玩笑','[]',3,self.now));self.db.commit()
  self.assertEqual(self.social.expressions('group:12345','明天八点医院复诊'),[])
  self.assertEqual(len(self.social.expressions('group:12345','开玩笑')),1)
  self.social.expression_sent('group:12345','有节目效果',self.now)
  self.assertEqual(self.social.expressions('group:12345','开玩笑'),[])
  self.assertEqual(len(self.social.expressions('group:12345','有节目效果')),1)
  self.assertEqual(self.social.expressions('group:99999','开玩笑'),[])
 def test_legacy_persona_copy_is_offered_once_for_exact_cas_deletion(self):
  self.db.execute('INSERT INTO companion_interests VALUES(?,?,?,?,?,?,?)',('*','的东西',3,4,self.now,'legacy','persona'));self.db.commit()
  products=memory_products.snapshot(self.db,'bot_shared',[]);self.assertEqual(len(products['interest']),1)
  row=products['interest'][0];before={'memory_state':{'products':products,'subjects':[]}}
  op={'kind':'interest','op':'replace','target':row['target'],'expected_version':row['version'],'value':{'weight':2}}
  self.assertEqual(memory_products.apply(self.db,'bot_shared',{}, {'memory_operations':[op]},before,self.now),0)
  op['op']='delete';op.pop('value')
  with self.db:self.assertEqual(memory_products.apply(self.db,'bot_shared',{}, {'memory_operations':[op]},before,self.now),1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_interests').fetchone()[0],0)
  with self.assertRaisesRegex(ValueError,'snapshot_changed'):
   memory_products.apply(self.db,'bot_shared',{}, {'memory_operations':[op]},before,self.now)
 def test_coverage_receipt_counts_all_39_candidates_without_copying_values(self):
  audit=Audit('bot_shared',1,'summary',{})
  prompt={'existing_memory':{'facts':[{'value':'private-text'} for _ in range(39)],'relations':[], 'products':{}},'core_note':{'text':'private-note'},'previous_commitments_visibility':{'total':39,'shown':39,'omitted':0}}
  audit.input_coverage(prompt)
  with self.db:audit.persist(self.db,'committed','test')
  import json
  raw=self.db.execute('SELECT counts FROM memory_operation_audits').fetchone()[0];counts=json.loads(raw)
  self.assertEqual(counts['facts']['input_coverage']['offered'],39)
  self.assertEqual(counts['facts']['input_coverage']['omitted'],0)
  self.assertNotIn('private-text',raw);self.assertNotIn('private-note',raw)

if __name__=='__main__':unittest.main()
