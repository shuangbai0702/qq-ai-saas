import sys,time,json,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from initiative import incoming,candidates
from data_admin import run

class ConfigurationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.file=Path(self.tmp.name)/'behavior.sqlite';self.store=BehaviorStore(self.file)
  self.cfg={'proactive':True,'behavior':{'proactiveTargets':['private:12345678'],'quietStart':0,'quietEnd':0,'silenceSeconds':60,'proactiveIntervalSeconds':60,'proactiveChance':.25,'dailyProactiveLimit':3,'maxUnanswered':1}}
  incoming(self.store.db,'private:12345678','12345678','900001','你好',100)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def test_probability_is_not_amplified_by_polling(self):
  calls=[]
  def rng():calls.append(1);return .8
  for now in range(200,260,5):self.assertEqual(candidates(self.store.db,self.cfg,now,rng),[])
  self.assertEqual(len(calls),1)
  candidates(self.store.db,self.cfg,260,rng);self.assertEqual(len(calls),2)
 def test_failed_probability_window_survives_restart(self):
  candidates(self.store.db,self.cfg,200,lambda:.8);self.store.close();self.store=BehaviorStore(self.file)
  self.assertEqual(candidates(self.store.db,self.cfg,205,lambda:0),[])
 def test_changed_probability_applies_without_waiting_for_old_window(self):
  candidates(self.store.db,self.cfg,200,lambda:.8)
  self.cfg['behavior']['proactiveChance']=1
  self.assertEqual(len(candidates(self.store.db,self.cfg,205,lambda:0)),1)
 def test_accepted_window_stays_available_during_reconnection(self):
  self.assertEqual(len(candidates(self.store.db,self.cfg,200,lambda:0)),1)
  self.assertEqual(len(candidates(self.store.db,self.cfg,205,lambda:.99)),1)
 def test_authorized_target_not_hidden_behind_hundred_other_contacts(self):
  for i in range(120):incoming(self.store.db,'private:'+str(50000000+i),str(50000000+i),'900001','你好',200+i)
  self.assertEqual(len(candidates(self.store.db,self.cfg,500,lambda:0)),1)
 def test_cooldown_does_not_consume_random_interjection_quota(self):
  cfg={'trigger':'mention','behavior':{'cooldownSeconds':60,'quietStart':0,'quietEnd':0,'randomReplyProbability':.2,'dailyProactiveLimit':3}}
  self.store.last['group:12345']=200
  self.assertFalse(self.store.eligible(cfg,'group:12345','12345',False,'聊天',205,rng=lambda:0))
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
  self.assertTrue(self.store.eligible(cfg,'group:12345','12345',False,'聊天',260,rng=lambda:0))
  self.assertEqual(self.store.db.execute('SELECT sum(count) FROM deliveries').fetchone()[0],1)
 def test_enabling_proactive_does_not_implicitly_enable_interjections(self):
  self.assertFalse(self.store.eligible({'proactive':True,'behavior':{'randomReplyProbability':0}},'group:12345','12345',False,'聊天',200,rng=lambda:.01))
 def test_clear_learning_allows_learning_from_new_samples(self):
  scope='group:12345';self.store.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',(scope,'有节目效果','轻松玩笑','[1,2]',1,time.time()));self.store.db.execute('INSERT INTO social_pattern_blocks VALUES(?,?)',(scope,'有节目效果'));self.store.db.commit()
  run(self.file,{'action':'clear_learning','scope':scope})
  for i in range(12):self.store.social.observe(scope,str(i%2),'真是有节目效果',str(i))
  batch=self.store.social.learning_batch(scope)
  result={'patterns':[{'expression':'有节目效果','situation':'轻松玩笑','source_ids':[batch[0]['id'],batch[1]['id']]}]}
  self.assertEqual(self.store.social.accept_learning(scope,batch,json.dumps(result)),1)
 def test_person_filter_also_filters_relationships(self):
  self.store.observe('group:12345','123456','你好','e1');self.store.observe('group:12345','654321','谢谢','e2')
  data=run(self.file,{'action':'list','subject':'654321'})
  self.assertEqual([r['subject'] for r in data['relations']],['654321'])

if __name__=='__main__':unittest.main(verbosity=2)
