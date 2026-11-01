import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from initiative import incoming,candidates,reserve,outgoing,split_text
import autonomy
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
 def test_chat_does_not_create_or_override_operator_pause(self):
  incoming(self.s.db,'private:12345678','12345678','90000001','不要主动找我',150);self.assertTrue(candidates(self.s.db,self.cfg,250))
  self.s.db.execute('UPDATE contacts SET opted_out=1');self.s.db.commit()
  incoming(self.s.db,'private:12345678','12345678','90000001','可以主动找我',300);self.assertEqual(candidates(self.s.db,self.cfg,400),[])
 def test_group_whitelist(self):
  incoming(self.s.db,'group:777777','12345678','90000001','你好',100);self.cfg['behavior']['proactiveTargets']=['group:777777'];self.cfg['groups']=['888888'];self.assertEqual(candidates(self.s.db,self.cfg,200),[])
 def test_global_bot_quota(self):
  self.cfg['behavior']['proactiveTargets']+=['private:87654321'];incoming(self.s.db,'private:87654321','87654321','90000001','你好',90);item=candidates(self.s.db,self.cfg,200)[0];reserve(self.s.db,self.cfg,item,200);self.assertEqual(candidates(self.s.db,self.cfg,300),[])
 def test_dice_zero_token_gate(self):self.assertEqual(candidates(self.s.db,{**self.cfg,'behavior':{**self.cfg['behavior'],'proactiveChance':0}},200),[])
 def test_model_cooldown_is_temporary_and_restart_preserves_pause(self):
  incoming(self.s.db,'private:12345678','12345678','90000001','晚安',150)
  self.assertEqual(self.s.db.execute('SELECT opted_out FROM contacts').fetchone()[0],0)
  self.assertTrue(candidates(self.s.db,self.cfg,300),'a word alone cannot impose a fixed eight-hour pause')
  with self.s.db:autonomy.record_decision(self.s.db,self.cfg,'private:12345678','model1',{'cooldown_intervals':2,'cooldown_reason':'busy'},False,250,'plan')
  self.assertEqual(candidates(self.s.db,self.cfg,300),[])
  self.s.close();self.s=BehaviorStore(Path(self.tmp.name)/'a.db')
  self.assertEqual(candidates(self.s.db,self.cfg,301),[])
  incoming(self.s.db,'private:12345678','12345678','90000001','我回来了',400)
  self.assertTrue(candidates(self.s.db,self.cfg,500))
 def test_ambient_group_goodnight_cannot_disable_proactive(self):
  incoming(self.s.db,'group:777777','12345678','90000001','晚安',100,directed=False)
  self.cfg['behavior']['proactiveTargets']=['group:777777'];self.cfg['behavior'].update(groupParticipationEnabled=True,groupParticipationGroups=['777777'])
  self.assertTrue(candidates(self.s.db,self.cfg,200))
 def test_new_pause_invalidates_reserved_candidate(self):
  item=candidates(self.s.db,self.cfg,200)[0]
  incoming(self.s.db,'private:12345678','12345678','90000001','晚安',201)
  self.assertFalse(reserve(self.s.db,self.cfg,item,202))
 def test_split_preserves_code(self):self.assertEqual(split_text('```js\na();\n```'),['```js\na();\n```']);self.assertEqual(split_text('你好呀。今天吃什么？'),['你好呀。今天吃什么？']);self.assertEqual(split_text('你好呀。\n今天吃什么？'),['你好呀。','今天吃什么？']);self.assertEqual(split_text('一\n二\n三\n四\n五'),['一','二','三','四\n五'])
if __name__=='__main__':unittest.main(verbosity=2)
