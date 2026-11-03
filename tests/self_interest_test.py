import json,sqlite3,sys,tempfile,time,unittest,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import companion_state as cs

class ReflectionContract(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'memory.sqlite';self.db=sqlite3.connect(self.path);cs.initialize(self.db);self.now=time.time();self.cfg={'systemPrompt':'喜欢喝茶。','behavior':{'selfDevelopment':True}};self.evidence=[{'source':'existing_companion_state','reference':'self:actual-read-fixture'}]
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def choose(self,topic='茶叶',operation='explore',action=None,reason='actual owned reading made this worth exploring',evidence=None):return cs.reflect_interest(self.db,topic,operation,reason,action or str(uuid.uuid4()),self.evidence if evidence is None else evidence,self.now)
 def test_restart_idempotence_scope_and_persona(self):
  action=str(uuid.uuid4());one=self.choose(action=action);self.assertEqual(one,self.choose(action=action));self.assertEqual(one['heat'],.4)
  self.assertEqual(one,self.choose(action=action,evidence=self.evidence+[{'source':'existing_memory','reference':'another actual read'}]))
  self.db.close();self.db=sqlite3.connect(self.path);cs.initialize(self.db)
  self.assertEqual([i['topic'] for i in cs.context(self.db,self.cfg,'self',self.now)['interests'] if i['source']=='autonomous_choice'],['茶叶'])
  self.assertTrue(any(i['source']=='autonomous_choice' and i['source_scope']=='self' for i in cs.context(self.db,self.cfg,'private:12345678',self.now)['interests']))
  cs.note_interest(self.db,'group:55555','只在本群热议',self.now,'local evidence','conversation_heat')
  self.assertFalse(any(i['topic']=='只在本群热议' for i in cs.context(self.db,self.cfg,'private:12345678',self.now)['interests']))
  self.assertTrue(any(i['source']=='persona' for i in cs.context(self.db,self.cfg,'*',self.now)['interests']))
  self.choose(operation='drop');self.assertFalse(any(i['source']=='autonomous_choice' for i in cs.context(self.db,self.cfg,'self',self.now)['interests']))
  self.assertTrue(any(i['source']=='persona' for i in cs.context(self.db,self.cfg,'*',self.now)['interests']))
 def test_actual_read_required(self):
  for e in [[],[{'source':'model_invention','reference':'not a read'}],[{'source':'existing_memory','reference':''}]]:
   with self.assertRaisesRegex(ValueError,'requires_actual_read'):self.choose(evidence=e)
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_interests').fetchone()[0],0)
 def test_identity_conflict_and_work_limit(self):
  action=str(uuid.uuid4());self.choose(action=action)
  with self.assertRaisesRegex(ValueError,'identity_conflict'):self.choose(action=action,operation='drop')
  self.choose(topic='音乐',action=action);self.choose(topic='绘画',action=action)
  with self.assertRaisesRegex(ValueError,'work_limit'):self.choose(topic='园艺',action=action)
 def test_audit_failure_rolls_back_canonical_interest(self):
  self.db.execute("CREATE TRIGGER controlled_failure BEFORE INSERT ON companion_reflections BEGIN SELECT RAISE(ABORT,'controlled failure');END")
  with self.assertRaises(sqlite3.IntegrityError):self.choose()
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_interests').fetchone()[0],0)
 def test_receipts_are_bounded_and_do_not_duplicate_raw_reading(self):
  for _ in range(520):self.choose()
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_reflections').fetchone()[0],512)
  self.assertEqual(self.db.execute('SELECT count(*) FROM companion_interests').fetchone()[0],1)
  result=json.loads(self.db.execute('SELECT result FROM companion_reflections LIMIT 1').fetchone()[0]);self.assertNotIn('actual-read-fixture',json.dumps(result))
 def test_topic_and_operation_boundaries(self):
  for topic in ['API Key=credential','QQ2324509593','https://example.test','x','a\nb']:
   with self.assertRaises(ValueError):self.choose(topic=topic)
  self.choose();self.assertEqual(self.choose(operation='cool_down')['heat'],0)
  self.choose(topic='系统设计');self.choose(topic='与主人聊天')

unittest.main()
