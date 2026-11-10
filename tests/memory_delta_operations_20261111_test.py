import json,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from companion_insights import snapshot,apply_semantic_memory
from memory_scope import fact_rows
from memory_products import snapshot as products,apply as apply_products

P='12345678';S='bot_shared';CFG={'memory':True,'memoryAutoCapture':True,'selfDevelopment':True}
class Deltas(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite');self.db=self.store.db
  self.payload=[{'event':'input','sender_id':P,'user':'真实经历','bot':'真实表达'}]
 def tearDown(self):self.store.close();self.temp.cleanup()
 def before(self):return snapshot(self.db,S,{P},CFG)
 def apply(self,data,before=None):return apply_semantic_memory(self.db,self.store.memory,S,CFG,self.payload,data,before or self.before(),now=100)
 def test_fact_create_replace_delete_with_short_version_and_untouched_other_fact(self):
  self.store.memory.remember('private:'+P,P,'其他事实','保持','original',1);self.db.commit()
  with self.db:self.assertEqual(self.apply({'fact_operations':[{'op':'create','scope':S,'subject':P,'name':'饮料','expected_version':None,'value':'红茶'}]}),1)
  old=self.before();version=next(r['version'] for r in old['memory_state']['fact_versions'] if r['name']=='饮料')
  with self.db:self.assertEqual(self.apply({'fact_operations':[{'op':'replace','scope':S,'subject':P,'name':'饮料','expected_version':version,'value':'咖啡'}]},old),1)
  with self.db:self.assertEqual(self.apply({'fact_operations':[{'op':'append','scope':S,'subject':P,'name':'饮料','expected_version':version,'value':'牛奶'}]}),0)
  latest=self.before();version=next(r['version'] for r in latest['memory_state']['fact_versions'] if r['name']=='饮料')
  with self.db:self.assertEqual(self.apply({'fact_operations':[{'op':'delete','scope':S,'subject':P,'name':'饮料','expected_version':version}]}),1)
  self.assertEqual([(r[0],r[1]) for r in fact_rows(self.db,S,P)],[('其他事实','保持')])
  self.assertEqual(self.db.execute("SELECT count(*) FROM facts WHERE name='饮料'").fetchone(),(0,))
 def test_relation_create_replace_delete_does_not_edit_identity_or_other_person(self):
  base={'scope':S,'subject':P,'expected_version':None}
  with self.db:self.assertEqual(self.apply({'relation_operations':[{**base,'op':'create','affinity':4,'trust':.6,'impression':'坦诚'}]}),1)
  state=self.before()['memory_state'];version=state['relation_versions'][0]['version']
  with self.db:self.assertEqual(self.apply({'relation_operations':[{**base,'op':'replace','expected_version':version,'affinity':2,'trust':.4,'impression':'新的综合理解'}]}),1)
  current=self.before();version=current['memory_state']['relation_versions'][0]['version']
  with self.db:self.assertEqual(self.apply({'relation_operations':[{**base,'op':'delete','expected_version':version}]}),1)
  self.assertIsNone(self.db.execute('SELECT subject FROM relations').fetchone())
 def test_interest_targeted_replace_delete_create_preserves_observations_and_persona(self):
  self.db.execute("INSERT INTO companion_interests VALUES('self','天文',3,8,1,'real observations','autonomous_choice')")
  self.db.execute("INSERT INTO companion_interests VALUES('self','固定喜好',4,9,1,'persona','persona')");self.db.commit()
  before=self.before();offered=before['memory_state']['products']['interest'][0]
  target={'scope':'self','topic':'天文'}
  with self.db:self.assertEqual(self.apply({'memory_operations':[{'kind':'interest','op':'replace','target':target,'expected_version':offered['version'],'value':{'weight':1}}]},before),1)
  self.assertEqual(self.db.execute("SELECT hits,evidence,weight FROM companion_interests WHERE topic='天文'").fetchone(),(8,'real observations',1))
  with self.db:self.assertEqual(self.apply({'memory_operations':[{'kind':'interest','op':'create','target':{'scope':'self','topic':'海洋'},'expected_version':None,'value':{'weight':2}}]}),1)
  self.assertEqual(self.db.execute("SELECT hits FROM companion_interests WHERE topic='海洋'").fetchone(),(0,))
  before=self.before();row=next(r for r in before['memory_state']['products']['interest'] if r['target']==target)
  with self.db:self.assertEqual(self.apply({'memory_operations':[{'kind':'interest','op':'delete','target':target,'expected_version':row['version']}]}),1)
  self.assertIsNotNone(self.db.execute("SELECT 1 FROM companion_interests WHERE topic='固定喜好'").fetchone())
 def test_stale_product_result_rolls_back_with_existing_transaction(self):
  self.db.execute("INSERT INTO companion_interests VALUES('self','天文',3,8,1,'real','autonomous_choice')");self.db.commit()
  before=self.before();row=before['memory_state']['products']['interest'][0]
  self.db.execute("UPDATE companion_interests SET weight=4");self.db.commit()
  with self.assertRaisesRegex(ValueError,'snapshot_changed'):
   with self.db:self.apply({'memory_operations':[{'kind':'interest','op':'delete','target':row['target'],'expected_version':row['version']}]},before)
  self.assertEqual(self.db.execute('SELECT weight FROM companion_interests').fetchone(),(4,))
 def test_motive_create_remains_model_opinion_not_accepted_user_task(self):
  target={'scope':S,'subject':P,'topic':'一起了解天文'}
  with self.db:self.assertEqual(self.apply({'memory_operations':[{'kind':'motive','op':'create','target':target,'expected_version':None,'value':{'status':'open','due':200,'expires':300}}]}),1)
  row=self.db.execute('SELECT evidence FROM companion_motives').fetchone();self.assertEqual(json.loads(row[0])['author'],'model_plan')
  self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_handoffs').fetchone(),(0,))
 def test_expression_change_requires_existing_knowledge_and_respects_operator_block(self):
  self.db.execute("INSERT INTO social_patterns VALUES('group:55555','好耶','开心时','[]',2,1)");self.db.commit()
  before=self.before();row=before['memory_state']['products']['expression'][0]
  op={'kind':'expression','op':'replace','target':row['target'],'expected_version':row['version'],'value':{'situation':'熟悉的人分享好消息时','weight':1}}
  with self.db:self.assertEqual(self.apply({'memory_operations':[op]},before),1)
  before=self.before();op['expected_version']=before['memory_state']['products']['expression'][0]['version']
  self.db.execute("INSERT INTO social_pattern_blocks VALUES('group:55555','好耶')");self.db.commit()
  with self.db:self.assertEqual(self.apply({'memory_operations':[op]},before),0)
 def test_core_note_three_verbs_and_append_rejected(self):
  ref=self.store.reflection;event={'event':'input','sender_id':P}
  with self.db:self.assertEqual(ref.update_core_note({'core_note':{'op':'create','text':'表达直率','events':['input']}},ref.core_note(),[event],[],S),1)
  with self.db:self.assertEqual(ref.update_core_note({'core_note':{'op':'append','text':'再加一点','events':['input']}},ref.core_note(),[event],[],S),0)
  with self.db:self.assertEqual(ref.update_core_note({'core_note':{'op':'replace','text':'表达直率而温和','events':['input']}},ref.core_note(),[event],[],S),1)
  with self.db:self.assertEqual(ref.update_core_note({'core_note':{'op':'delete','events':['input']}},ref.core_note(),[event],[],S),1)
  self.assertEqual(ref.core_note()['text'],'')

if __name__=='__main__':unittest.main()
