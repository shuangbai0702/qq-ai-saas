import json,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore

class SharingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.a=BehaviorStore(Path(self.tmp.name)/'a'/'behavior.sqlite');self.b=BehaviorStore(Path(self.tmp.name)/'b'/'behavior.sqlite')
  self.a.db.execute("INSERT INTO social_patterns VALUES('group:10000','有节目效果','开玩笑','[]',2,?)",(time.time(),));self.a.db.commit()
  self.cfg={'styleLearning':True,'expressionShareGroups':['10000','20000']}
 def tearDown(self):self.a.close();self.b.close();self.tmp.cleanup()
 def hints(self,scope,cfg=None,store=None):return (store or self.a).social.context(scope,'开玩笑',self.cfg if cfg is None else cfg)['expression_hints']
 def test_default_local_and_explicit_link(self):
  self.assertFalse(self.hints('group:20000',{'styleLearning':True}));self.assertTrue(self.hints('group:20000'));self.assertFalse(self.hints('group:30000'));self.assertFalse(self.hints('private:10000'));self.assertFalse(self.hints('group:20000',store=self.b))
 def test_scope_blocks_and_expiry_still_apply(self):
  for scope in ['group:10000','group:20000']:
   self.a.db.execute('INSERT INTO social_pattern_blocks VALUES(?,?)',(scope,'有节目效果'));self.a.db.commit();self.assertFalse(self.hints('group:20000'));self.a.db.execute('DELETE FROM social_pattern_blocks');self.a.db.commit()
  self.a.db.execute('UPDATE social_patterns SET updated=?',(time.time()-31*86400,));self.a.db.commit();self.assertFalse(self.hints('group:20000'))
 def test_botwide_includes_other_groups_but_never_private_or_other_bots(self):
  cfg={'styleLearning':True,'expressionSharing':'bot'}
  self.assertTrue(self.hints('group:30000',cfg));self.assertFalse(self.hints('private:10000',cfg));self.assertFalse(self.hints('group:30000',cfg,store=self.b))
  self.assertFalse(self.hints('group:20000',{**self.cfg,'expressionSharing':'local'}))
 def test_duplicate_expression_one_hint_and_same_person_memory_stays_isolated(self):
  self.a.db.execute("INSERT INTO social_patterns VALUES('group:20000','有节目效果','开玩笑','[]',1,?)",(time.time(),));self.a.db.commit();self.assertEqual(len(self.hints('group:20000')),1)
  self.a.observe('group:10000','12345','记住：饮料=绿茶','e',time.time())
  same=self.a.observe('group:20000','12345','喝什么','e2',time.time())['facts'];self.assertEqual(same[0]['value'],'绿茶');self.assertEqual(same[0]['source_scope'],'group:10000')
  self.assertFalse(self.a.observe('group:20000','67890','喝什么','e3',time.time())['facts'])
  self.assertFalse(self.b.observe('group:20000','12345','喝什么','e4',time.time())['facts'])

if __name__=='__main__':unittest.main(verbosity=2)
