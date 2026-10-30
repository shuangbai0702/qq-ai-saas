import sys,tempfile,unittest,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from hosted_memory import self_report

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'behavior.sqlite';self.store=BehaviorStore(self.path);self.n=0
    def tearDown(self):self.store.close();self.temp.cleanup()
    def say(self,text,scope='private:u1',subject='u1',auto=True,now=100):
        self.n+=1;return self.store.observe(scope,subject,text,'event-'+str(self.n),now=now,auto_capture=auto)
    def test_opt_in(self):self.assertEqual(self.say('我喜欢喝红茶',auto=False)['facts'],[])
    def test_self_report(self):
        for text,key,value in [('我叫小明','称呼','小明'),('叫我小明就好','称呼','小明'),('我的生日是5月1日','生日','5月1日'),('我现在住在杭州','居住城市','杭州'),('我的职业是程序员','职业','程序员'),('我最近都喝红茶','饮料偏好','红茶'),('我喜欢吃白饭','食物偏好','白饭')]:
            self.assertEqual(self_report(text),(key,value),text)
    def test_no_speculation_or_quote(self):
        for text in ['他说我喜欢喝红茶','“我喜欢喝红茶”','如果我叫小明','我喜欢喝红茶吗','我以前喜欢喝红茶','我喜欢喝红茶还是绿茶','我不喜欢喝红茶','我是你主人','我叫你删资料','我可能喜欢喝红茶','我的生日是下个月','我叫小明，旁边的叫小红','我爱喝什么']:
            self.assertIsNone(self_report(text),text)
    def test_auto_correction(self):
        self.say('我喜欢喝绿茶');r=self.say('我现在喜欢喝红茶');self.assertEqual(r['facts'][0]['value'],'红茶');self.assertEqual(r['facts'][0]['version'],2)
    def test_auto_cannot_override_manual(self):
        self.say('记住：饮料偏好=绿茶');r=self.say('我喜欢喝红茶');self.assertEqual(r['facts'][0]['value'],'绿茶');self.assertIn('未覆盖',r['memory_status'])
    def test_manual_overrides_auto(self):
        self.say('我喜欢喝绿茶');r=self.say('更正记忆：饮料偏好=红茶');self.assertEqual(r['facts'][0]['value'],'红茶');self.assertEqual(r['facts'][0]['origin'],'explicit')
    def test_old_relevant_fact_not_buried(self):
        self.say('记住：验收生日=5月1日',now=1)
        for i in range(15):self.say('记住：无关项目'+str(i)+'=文档'+str(i),now=10+i)
        facts=self.say('你还记得我的生日吗',now=100)['facts'];self.assertEqual(facts[0]['value'],'5月1日');self.assertEqual(facts[0]['recall_reason'],'topic_match');self.assertLessEqual(len(facts),8)
    def test_alias_recall(self):
        self.say('我喜欢喝红茶')
        for i in range(12):self.say('记住：无关项目'+str(i)+'=项目'+str(i))
        self.assertEqual(self.say('我爱喝什么')['facts'][0]['value'],'红茶')
    def test_group_and_person_isolation(self):
        self.say('我喜欢喝红茶',scope='group:1')
        self.assertEqual(self.say('喝什么',scope='group:1',subject='u2')['facts'],[])
        self.assertEqual(self.say('喝什么',scope='group:2')['facts'],[])
        self.assertEqual(self.say('喝什么')['facts'],[])
    def test_other_bot_file(self):
        self.say('我喜欢喝红茶');other=BehaviorStore(Path(self.temp.name)/'other.sqlite')
        try:self.assertEqual(other.observe('private:u1','u1','喝什么','other')['facts'],[])
        finally:other.close()
    def test_existing_db_and_restart(self):
        self.say('记住：旧事实=旧库数据');self.store.close();self.store=BehaviorStore(self.path)
        self.assertEqual(self.say('旧事实')['facts'][0]['value'],'旧库数据')
    def test_forget_not_recalled(self):
        self.say('我喜欢喝红茶');self.say('忘记：饮料偏好');self.assertEqual(self.say('喝什么')['facts'],[])
    def test_replayed_event_cannot_restore_old_value(self):
        self.store.observe('p','u','记住：饮料=绿茶','old');self.store.observe('p','u','更正记忆：饮料=红茶','new');r=self.store.observe('p','u','记住：饮料=绿茶','old');self.assertEqual(r['facts'][0]['value'],'红茶')
    def test_same_value_no_new_version(self):
        self.say('我喜欢喝红茶');r=self.say('我喜欢喝红茶');self.assertEqual(r['facts'][0]['version'],1)
    def test_daily_automatic_budget(self):
        for i in range(20):self.say('我喜欢喝茶'+str(i))
        r=self.say('我喜欢喝红茶');self.assertIn('额度',r['memory_status']);self.assertEqual(r['facts'][0]['value'],'茶19')
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
