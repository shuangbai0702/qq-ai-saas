import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from initiative import incoming,candidates,reserve,outgoing,split_text
class InitiativeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=BehaviorStore(Path(self.tmp.name)/'a.db');self.cfg={'proactive':True,'behavior':{'proactiveTargets':['private:12345678'],'quietStart':0,'quietEnd':0,'silenceSeconds':60,'proactiveIntervalSeconds':60,'proactiveChance':1,'dailyProactiveLimit':1,'maxUnanswered':1}}
  incoming(self.s.db,'private:12345678','12345678','90000001','可以主动找我',100)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def test_silence_before_model(self):self.assertEqual(candidates(self.s.db,self.cfg,150),[]);self.assertEqual(len(candidates(self.s.db,self.cfg,200)),1)
 def test_no_unknown_target(self):self.cfg['behavior']['proactiveTargets']=['private:87654321'];self.assertEqual(candidates(self.s.db,self.cfg,200),[])
 def test_reservation_and_unanswered(self):
  item=candidates(self.s.db,self.cfg,200)[0];self.assertTrue(reserve(self.s.db,self.cfg,item,200));self.assertEqual(candidates(self.s.db,self.cfg,300),[])
 def test_daily_quota_survives_restart(self):
  reserve(self.s.db,self.cfg,candidates(self.s.db,self.cfg,200)[0],200);self.s.db.close();self.s=BehaviorStore(Path(self.tmp.name)/'a.db');incoming(self.s.db,'private:12345678','12345678','90000001','你好',300);self.assertEqual(candidates(self.s.db,self.cfg,400),[])
 def test_new_input_invalidates_old_candidate(self):
  item=candidates(self.s.db,self.cfg,200)[0];incoming(self.s.db,'private:12345678','12345678','90000001','我回来了',201);self.assertFalse(reserve(self.s.db,self.cfg,item,202))
 def test_explicit_optout_and_restore(self):
  incoming(self.s.db,'private:12345678','12345678','90000001','不要主动找我',150);self.assertEqual(candidates(self.s.db,self.cfg,250),[]);incoming(self.s.db,'private:12345678','12345678','90000001','可以主动找我',300);self.assertTrue(candidates(self.s.db,self.cfg,400))
 def test_group_whitelist(self):
  incoming(self.s.db,'group:777777','12345678','90000001','你好',100);self.cfg['behavior']['proactiveTargets']=['group:777777'];self.cfg['groups']=['888888'];self.assertEqual(candidates(self.s.db,self.cfg,200),[])
 def test_global_bot_quota(self):
  self.cfg['behavior']['proactiveTargets']+=['private:87654321'];incoming(self.s.db,'private:87654321','87654321','90000001','你好',90);item=candidates(self.s.db,self.cfg,200)[0];reserve(self.s.db,self.cfg,item,200);self.assertEqual(candidates(self.s.db,self.cfg,300),[])
 def test_dice_zero_token_gate(self):self.assertEqual(candidates(self.s.db,{**self.cfg,'behavior':{**self.cfg['behavior'],'proactiveChance':0}},200),[])
 def test_split_preserves_code(self):self.assertEqual(split_text('```js\na();\n```'),['```js\na();\n```']);self.assertEqual(split_text('你好呀。今天吃什么？'),['你好呀。','今天吃什么？']);self.assertLessEqual(len(split_text('好。'*8)),3)
if __name__=='__main__':unittest.main(verbosity=2)
