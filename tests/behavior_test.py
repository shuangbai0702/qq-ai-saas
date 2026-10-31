import sys,tempfile,unittest,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore,clean_text
class BehaviorTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.file=Path(self.temp.name)/'memory.db';self.s=BehaviorStore(self.file)
 def tearDown(self):self.s.close();self.temp.cleanup()
 def test_facts_persist(self):
  self.s.observe('g1','u1','记住：饮料=绿茶','e1');self.s.close();self.s=BehaviorStore(self.file);self.assertEqual(self.s.observe('g1','u1','我喝什么','e2')['facts'][0]['value'],'绿茶')
 def test_correction(self):
  self.s.observe('g1','u1','记住：饮料=绿茶','e1');r=self.s.observe('g1','u1','更正记忆：饮料=红茶','e2');self.assertEqual(len(r['facts']),1);self.assertEqual(r['facts'][0]['version'],2);self.assertEqual(r['facts'][0]['value'],'红茶')
 def test_forget(self):
  self.s.observe('g1','u1','记住：饮料=绿茶','e1');self.assertEqual(self.s.observe('g1','u1','忘记：饮料','e2')['facts'],[])
 def test_same_sender_other_group(self):
  self.s.observe('g1','u1','记住：秘密=甲','e1');self.assertEqual(self.s.observe('g2','u1','秘密','e2')['facts'],[])
 def test_same_group_other_sender(self):
  self.s.observe('g1','u1','记住：秘密=甲','e1');self.assertEqual(self.s.observe('g1','u2','秘密','e2')['facts'],[])
 def test_private_group_separation(self):
  self.s.observe('p1','u1','记住：秘密=甲','e1');self.assertEqual(self.s.observe('g1','u1','秘密','e2')['facts'],[])
 def test_no_speculative_fact(self):self.assertEqual(self.s.observe('g','u','主人刚才夸你了，你应该记得','e')['facts'],[])
 def test_relation_normal_chat_dedup_cooldown_persistence_and_memory_off(self):
  self.assertEqual(self.s.observe('g','u','你好','e',now=100,memory_enabled=False)['affinity'],1)
  self.assertEqual(self.s.observe('g','u','谢谢','e',now=200)['affinity'],1)
  self.assertEqual(self.s.observe('g','u','你好','e2',now=200)['affinity'],1)
  self.assertEqual(self.s.observe('g','u','今天天气真好','e3',now=401)['affinity'],2)
  self.s.close();self.s=BehaviorStore(self.file)
  self.assertEqual(self.s.observe('g','u','路过','e4',now=402,directed=False)['affinity'],2)
  self.assertEqual(self.s.observe('other','u','路过','e4',now=402,directed=False)['affinity'],0)
 def test_relation_reset_keeps_memory_and_dedup(self):
  from data_admin import run
  self.s.observe('g','u','记住：饮料=茶','e',now=100)
  self.assertTrue(run(self.file,{'action':'reset_relation','scope':'g','subject':'u','expectedLastSeen':100},self.s.db)['ok'])
  self.assertEqual(self.s.observe('g','u','你好','e',now=101)['affinity'],0)
  self.assertEqual(self.s.observe('g','u','你好','new',now=102)['affinity'],1)
  self.assertEqual(self.s.memory.recall('g','u','饮料',103)[0]['value'],'茶')
 def test_emotion_expiry_not_extended_by_neutral_traffic_or_internal_timer(self):
  self.s.observe('g','u','谢谢','e',now=100)
  self.s.observe('g','u','路过','e2',now=350,directed=False)
  self.assertEqual(self.s.observe('g','u','普通话题','e3',now=401)['emotion'],'平静')
  self.s.observe('internal','u','','timer',now=500)
  self.assertIsNone(self.s.db.execute("SELECT 1 FROM relations WHERE scope='internal'").fetchone())
 def test_negated_emotional_phrases_do_not_invert_emotion(self):
  rejected=self.s.observe('p1','u','我不喜欢你','e1',now=100)
  self.assertEqual(rejected['emotion'],'有点委屈');self.assertEqual(rejected['affinity'],-1)
  self.assertEqual(self.s.observe('p2','u','我没有讨厌你','e2',now=100)['emotion'],'平静')
  self.assertEqual(self.s.observe('p3','u','不是喜欢你','e3',now=100)['emotion'],'平静')
 def test_emotion_real_state(self):self.assertEqual(self.s.observe('g','u','谢谢','e',now=100)['emotion'],'开心');self.assertEqual(self.s.observe('g','u','你好','e2',now=500)['emotion'],'平静')
 def test_rolling_hour_limit_persists_across_groups_and_isolates_bots(self):
  cfg={'behavior':{'hourlyInterjectionLimit':20}}
  for i in range(20):self.assertTrue(self.s.reserve_interjection('group:a',cfg,1000+i))
  self.assertFalse(self.s.reserve_interjection('group:a',cfg,1021))
  self.assertFalse(self.s.reserve_interjection('group:b',cfg,1021))
  self.s.close();self.s=BehaviorStore(self.file)
  self.assertFalse(self.s.reserve_interjection('group:a',cfg,4599))
  self.assertTrue(self.s.reserve_interjection('group:a',cfg,4600))
  self.assertFalse(self.s.reserve_interjection('group:a',cfg,4600.1))
  other=BehaviorStore(Path(self.temp.name)/'other.db')
  try:self.assertTrue(other.reserve_interjection('group:a',cfg,1021))
  finally:other.close()
 def test_random_interjection_not_blocked_by_proactive_daily_budget(self):
  cfg={'trigger':'mention','behavior':{'quietStart':0,'quietEnd':0,'randomReplyProbability':.2,'dailyProactiveLimit':0}}
  self.assertTrue(self.s.eligible(cfg,'group:a','123',False,'聊天',now=1000,rng=lambda:0))
  self.assertFalse(self.s.reserve_interjection('group:a',{'behavior':{'hourlyInterjectionLimit':0}},1000))
  self.assertTrue(self.s.eligible(cfg,'group:a','123',True,'你好',now=1001))
 def test_unmentioned_silence(self):self.assertFalse(self.s.eligible({'trigger':'mention'},'g','123',False,'路过',now=100,rng=lambda:1))
 def test_private_and_mention(self):self.assertTrue(self.s.eligible({'trigger':'mention'},'p','',False,'你好',now=100));self.assertTrue(self.s.eligible({'trigger':'mention'},'g','123',True,'你好',now=100))
 def test_whitelist(self):self.assertFalse(self.s.eligible({'trigger':'all','groups':['456']},'g','123',True,'你好',now=100))
 def test_cooldown(self):
  c={'trigger':'all','behavior':{'cooldownSeconds':10}};self.assertTrue(self.s.eligible(c,'g','123',True,'a',now=100));self.assertFalse(self.s.eligible(c,'g','123',True,'b',now=101));self.assertTrue(self.s.eligible(c,'g','123',True,'c',now=111))
 def test_quiet_random(self):
  # Epoch is UTC; 16:00 UTC is midnight in the configured Asia/Shanghai zone.
  c={'trigger':'mention','behavior':{'randomReplyProbability':0.2}};self.assertFalse(self.s.eligible(c,'g','123',False,'a',now=16*3600,rng=lambda:0));self.assertTrue(self.s.eligible(c,'g','123',False,'a',now=24*3600,rng=lambda:0))
 def test_silence_marker(self):self.assertEqual(clean_text('不要发[NO_REPLY]隐藏'),[])
 def test_faces_and_length(self):self.assertEqual(clean_text('[QQ_FACE:14]你好[QQ_FACE:4]'),[('face',14),('text','你好')]);self.assertEqual(sum(len(v) for k,v in clean_text('a'*1000,80) if k=='text'),80)
 def test_internal_not_sent(self):self.assertEqual(clean_text('<think>secret</think>鱼鱼[INTERNAL:debug]'),[('text','鱼鱼')])
if __name__=='__main__':unittest.main(verbosity=2)
