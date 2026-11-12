"""Real foreign memory writes cannot become new input for an existing draft."""
import json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'references/astrbot'),str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
from behavior import BehaviorStore
from bot_pipeline import decision_memory,decision_version
from group_freshness import guard_dispatch,PreDispatchRejected
from agreement_state import apply_update


class ScopeFreshness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'state.sqlite');self.db=self.store.db
        self.scope='group:12345';self.other='private:11111111';self.now=time.time();self.started=time.monotonic();self.calls=[]
        self.store.freshness.observe(self.scope,'original','11111111','原问题')
        self.cursor=self.store.freshness.cursor(self.scope)
        self.extra={'_hosted_freshness_cursors':{self.scope:self.cursor}}
        self.event=NS(get_extra=lambda key:self.extra.get(key))
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def guard(self):guard_dispatch(self.event,self.store,lambda:True,'send_group_msg',{'group_id':12345})
    async def check(self):
        async def review(data):self.calls.append(data);return '{"action":"keep"}'
        return await self.store.freshness.check(self.scope,self.cursor,self.started,'original','当前草稿','',review,lambda:True)
    async def test_foreign_put_correct_restore_changes_shared_context_but_not_current_draft(self):
        memory=self.store.memory
        memory.remember(self.scope,'11111111','地点','上海','one',self.now)
        before=decision_version(self.db,self.scope)
        memory.remember(self.other,'11111111','地点','北京','two',self.now+1)
        self.assertEqual(memory.correct(self.other,'11111111','地点','北京','广州','three','本人明确纠正',self.now+2),'已按本人证据纠正资料')
        self.assertNotEqual(before,decision_version(self.db,self.scope))
        # Cross-chat restore also mutates old local block rows. Original row
        # scope cannot identify the origin of the latest mutation.
        memory.correct(self.scope,'11111111','地点','广州','杭州','four','本人明确纠正',self.now+3)
        self.assertTrue(self.db.execute('SELECT 1 FROM memory_blocks WHERE scope=?',(self.scope,)).fetchone())
        memory.remember(self.other,'11111111','地点','广州','five',self.now+4)
        self.assertIsNone(self.db.execute('SELECT 1 FROM memory_blocks WHERE scope=?',(self.scope,)).fetchone())
        self.store.freshness.observe(self.other,'five','11111111','另一个会话的记忆变化')
        self.assertEqual((await self.check())['reason'],'unchanged');self.assertEqual(self.calls,[]);self.guard()
        projection=decision_memory(self.db,{'behavior':{'memory':True}},self.scope,'11111111',self.now+5)
        self.assertIn('广州',json.dumps(projection,ensure_ascii=False))
    async def test_foreign_agreement_even_with_current_audience_does_not_interrupt(self):
        before=decision_version(self.db,self.scope)
        _,error=apply_update(self.db,'propose','保持安静','11111111',['11111111','bot'],self.other,'a',audiences=[self.scope])
        self.assertEqual(error,'');self.assertNotEqual(before,decision_version(self.db,self.scope))
        self.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1')
        self.db.execute('INSERT INTO relations(scope,subject,affinity,trust,impression) VALUES(?,?,?,?,?)',(self.other,'11111111',2,.7,'新认知'))
        self.assertEqual((await self.check())['reason'],'unchanged');self.assertEqual(self.calls,[]);self.guard()
    async def test_actual_target_message_requires_review_and_final_dispatch_fence(self):
        self.store.freshness.observe(self.scope,'new','22222222','目标会话真实补充')
        with self.assertRaises(PreDispatchRejected):self.guard()
        decision=await self.check();self.assertEqual(decision['action'],'keep');self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0]['latest_messages'][0]['event'],'new')
        self.extra['_hosted_freshness_cursors'][self.scope]=decision['cursor'];self.guard()
        self.store.freshness.observe(self.scope,'later','22222222','复核后又来一条')
        with self.assertRaises(PreDispatchRejected):self.guard()


if __name__=='__main__':unittest.main()
