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
 def test_emotion_real_state(self):self.assertEqual(self.s.observe('g','u','谢谢','e',now=100)['emotion'],'开心');self.assertEqual(self.s.observe('g','u','你好','e2',now=500)['emotion'],'平静')
 def test_unmentioned_silence(self):self.assertFalse(self.s.eligible({'trigger':'mention'},'g','123',False,'路过',now=100))
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
