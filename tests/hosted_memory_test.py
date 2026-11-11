import sys,tempfile,unittest,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'behavior.sqlite';self.store=BehaviorStore(self.path);self.n=0
    def tearDown(self):self.store.close();self.temp.cleanup()
    def say(self,text,scope='private:u1',subject='u1',auto=True,now=100):
        self.n+=1;return self.store.observe(scope,subject,text,'event-'+str(self.n),now=now,auto_capture=auto)
    def capture(self,value,scope='private:u1',subject='u1'):
        # Exercise the storage contract after a semantic writer has selected
        # the fact; ordinary chat must never run regex extraction again.
        self.n+=1
        status=self.store.memory.remember(scope,subject,'饮料偏好',value,'semantic-'+str(self.n),100,origin='self_report')
        self.store.db.commit()
        return {'facts':self.store.memory.recall(scope,subject,'喝什么',100),'memory_status':status}
    def test_chat_does_not_implicitly_extract_keywords(self):
        for text in ('我喜欢喝红茶','我叫小明','我的生日是5月1日','他说我喜欢喝红茶','我不喜欢喝红茶'):
            with self.subTest(text=text):self.assertEqual(self.say(text)['facts'],[])
    def test_opt_in(self):self.assertEqual(self.say('我喜欢喝红茶',auto=False)['facts'],[])
    def test_auto_correction(self):
        self.capture('绿茶');r=self.capture('红茶');self.assertEqual(r['facts'][0]['value'],'红茶');self.assertEqual(r['facts'][0]['version'],2)
    def test_auto_cannot_override_manual(self):
        self.say('记住：饮料偏好=绿茶');r=self.capture('红茶');self.assertEqual(r['facts'][0]['value'],'绿茶');self.assertIn('未覆盖',r['memory_status'])
    def test_manual_overrides_auto(self):
        self.say('我喜欢喝绿茶');r=self.say('更正记忆：饮料偏好=红茶');self.assertEqual(r['facts'][0]['value'],'红茶');self.assertEqual(r['facts'][0]['origin'],'explicit')
    def test_old_relevant_fact_not_buried(self):
        self.say('记住：验收生日=5月1日',now=1)
        for i in range(15):self.say('记住：无关项目'+str(i)+'=文档'+str(i),now=10+i)
        facts=self.say('你还记得我的生日吗',now=100)['facts'];self.assertEqual(facts[0]['value'],'5月1日');self.assertEqual(facts[0]['recall_reason'],'topic_match');self.assertLessEqual(len(facts),8)
    def test_alias_recall(self):
        self.capture('红茶')
        for i in range(12):self.say('记住：无关项目'+str(i)+'=项目'+str(i))
        self.assertEqual(self.say('我爱喝什么')['facts'][0]['value'],'红茶')
    def test_group_and_person_isolation(self):
        self.say('我喜欢喝红茶',scope='group:1')
        self.assertEqual(self.say('喝什么',scope='group:1',subject='u2')['facts'],[])
        self.assertEqual(self.say('喝什么',scope='group:2')['facts'],[])
        self.assertEqual(self.say('喝什么')['facts'],[])
    def test_other_bot_file(self):
        self.capture('红茶');other=BehaviorStore(Path(self.temp.name)/'other.sqlite')
        try:self.assertEqual(other.observe('private:u1','u1','喝什么','other')['facts'],[])
        finally:other.close()
    def test_existing_db_and_restart(self):
        self.say('记住：旧事实=旧库数据');self.store.close();self.store=BehaviorStore(self.path)
        self.assertEqual(self.say('旧事实')['facts'][0]['value'],'旧库数据')
    def test_forget_not_recalled(self):
        self.capture('红茶');self.say('忘记：饮料偏好');self.assertEqual(self.say('喝什么')['facts'],[])
    def test_replayed_event_cannot_restore_old_value(self):
        self.store.observe('p','u','记住：饮料=绿茶','old');self.store.observe('p','u','更正记忆：饮料=红茶','new');r=self.store.observe('p','u','记住：饮料=绿茶','old');self.assertEqual(r['facts'][0]['value'],'红茶')
    def test_same_value_no_new_version(self):
        self.capture('红茶');r=self.capture('红茶');self.assertEqual(r['facts'][0]['version'],1)
    def test_daily_automatic_budget(self):
        for i in range(20):self.capture('茶'+str(i))
        r=self.capture('红茶');self.assertIn('额度',r['memory_status']);self.assertEqual(r['facts'][0]['value'],'茶19')
        self.assertEqual(self.say('更正记忆：饮料偏好=红茶')['facts'][0]['value'],'红茶')
    def test_prompt_budget(self):
        for i in range(10):self.say('记住：条目'+str(i)+'='+'长'*300)
        facts=self.say('条目')['facts'];self.assertLessEqual(sum(len(f['name'])+len(f['value'])for f in facts),1600)
    def test_event_requires_relevance(self):
        self.say('记住事件：昨天午餐=拉面',now=1)
        self.assertEqual(self.say('你好',now=86400*365)['facts'],[])
        r=self.say('午餐吃什么',now=86400*365)['facts'];self.assertEqual(r[0]['kind'],'event');self.assertEqual(r[0]['value'],'拉面')
    def test_version_retention(self):
        for i in range(25):self.say('更正记忆：项目='+str(i))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM facts').fetchone()[0],10)

if __name__=='__main__':unittest.main(verbosity=2)
