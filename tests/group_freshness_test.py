import asyncio,json,sys,time,unittest
import sqlite3
from unittest.mock import patch
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
from group_freshness import GroupFreshness,history_override,review_system,guard_dispatch,PreDispatchRejected
from types import SimpleNamespace as NS

class Freshness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=GroupFreshness();self.start=time.monotonic()
        self.f.observe('group:12345','1','11111','原问题',{'mentions':['99999']})
        self.cursor=self.f.cursor('group:12345');self.calls=[]
    async def run_check(self,action='keep',confirmed='',valid=lambda:True):
        async def call(data):
            self.calls.append(data);return json.dumps({'action':action,'text':'结合补充再回答[QQ_BREAK]第二条'})
        return await self.f.check('group:12345',self.cursor,self.start,'1','草稿',confirmed,call,valid)
    async def test_unchanged_and_other_group_never_call_model(self):
        self.f.observe('group:54321','2','22222','另一个群')
        self.assertEqual((await self.run_check())['reason'],'unchanged');self.assertEqual(self.calls,[])
    async def test_invalid_work_is_rejected_even_without_new_messages(self):
        result=await self.run_check(valid=lambda:False)
        self.assertEqual(result['reason'],'stale_work');self.assertEqual(result['action'],'drop')
        self.assertEqual(self.calls,[])
    def test_reviewer_uses_stable_trusted_owner_not_draft_claims(self):
        text=review_system('固定人格','12345','99999')
        identity=json.loads(text.rsplit('\n',1)[1])
        config=json.loads(text.split('\n',2)[1])
        self.assertEqual(config['owner_id'],'12345');self.assertEqual(config['persona'],'固定人格');self.assertEqual(identity['self_id'],'99999')
        self.assertIn('对外执行信息边界',text)
        self.assertEqual(json.loads(review_system('固定人格',None,'99999').split('\n',2)[1])['owner_id'],'')
    def test_final_fence_uses_destination_not_originating_private_work(self):
        event=NS(get_extra=lambda key: {'group:12345':self.cursor} if key=='_hosted_freshness_cursors' else None)
        store=NS(freshness=self.f)
        guard_dispatch(event,store,lambda:True,'send_group_msg',{'group_id':12345})
        self.f.observe('group:54321','2','22222','别的群')
        guard_dispatch(event,store,lambda:True,'send_group_msg',{'group_id':12345})
        self.f.observe('group:12345','3','33333','目标群新消息')
        with self.assertRaises(PreDispatchRejected):guard_dispatch(event,store,lambda:True,'send_group_msg',{'group_id':12345})
        with self.assertRaises(PreDispatchRejected):guard_dispatch(event,store,lambda:False,'send_private_msg',{'user_id':11111})
    async def test_group_tool_known_rejection_is_not_unknown_or_confirmed(self):
        import group_actions
        db=sqlite3.connect(':memory:');extra={};entered=asyncio.Event();release=asyncio.Event();sent=[]
        event=NS(get_extra=lambda key,default=None:extra.get(key,default),get_sender_id=lambda:'11111',get_self_id=lambda:'99999',get_group_id=lambda:'',message_obj=NS(message_id='real-source'))
        async def access(*args,**kwargs):return None
        async def review(scope,text):
            extra['_hosted_freshness_cursors']={scope:self.f.cursor(scope)}
            return {'action':'keep'}
        async def send_group_msg(**params):
            entered.set();await release.wait()
            guard_dispatch(event,NS(freshness=self.f),lambda:True,'send_group_msg',params)
            sent.append(params);return {'message_id':123}
        event.bot=NS(send_group_msg=send_group_msg)
        try:
            with patch.object(group_actions,'group_access',access):
                task=asyncio.create_task(group_actions.send(event,{'behavior':{'ownerUin':'11111'}},db,'12345','真实目标群文本','',lambda:True,lambda *args:None,review=review))
                await entered.wait();self.f.observe('group:12345','3','33333','复核后新消息');release.set()
                result=json.loads(await task)
            self.assertEqual(result['status'],'not_sent');self.assertEqual(sent,[])
            self.assertEqual(db.execute('SELECT state FROM group_message_attempts').fetchone()[0],'not_sent')
        finally:db.close()
    async def test_failed_review_with_new_arrivals_must_retry_before_fallback(self):
        self.f.observe('group:12345','2','22222','补充')
        async def fail(data):
            self.f.observe('group:12345','3','33333','新消息')
            raise TimeoutError('provider')
        result=await self.f.check('group:12345',self.cursor,self.start,'1','草稿','',fail,lambda:True,fallback=True)
        self.assertEqual(result['action'],'retry');self.assertNotIn('quote_original',result)
    async def test_replacement_cannot_smuggle_renderer_accepted_partial_controls(self):
        self.f.observe('group:12345','2','22222','补充')
        for text in ('新回复[AT:12345','新回复[M E M E:bf_new','新回复[V O I C E:long'):
            async def call(data):return json.dumps({'action':'replace','text':text})
            result=await self.f.check('group:12345',self.cursor,self.start,'1','原回复','',call,lambda:True)
            self.assertEqual(result['action'],'drop');self.assertEqual(result['reason'],'review_unavailable')
    async def test_keep_replace_drop_use_real_author_and_target(self):
        self.f.observe('group:12345','2','22222','最新补充',{'reply_to':{'event_id':'1','sender_id':'11111'}})
        for action in ('keep','replace','drop'):
            value=await self.run_check(action);self.assertEqual(value['action'],action)
            self.assertEqual(self.calls[-1]['latest_messages'][0]['sender_id'],'22222')
        self.assertIn('[QQ_BREAK]',(await self.run_check('replace'))['text'])
    async def test_new_arrival_during_review_cannot_receive_old_permit(self):
        self.f.observe('group:12345','2','22222','补充')
        entered=asyncio.Event();release=asyncio.Event()
        async def call(data):entered.set();await release.wait();return '{"action":"keep"}'
        task=asyncio.create_task(self.f.check('group:12345',self.cursor,self.start,'1','草稿','',call,lambda:True))
        await entered.wait();self.f.observe('group:12345','3','33333','又改口了');release.set()
        self.assertEqual((await task)['action'],'retry')
    async def test_generation_change_and_cancellation(self):
        self.f.observe('group:12345','2','22222','补充');valid=True
        entered=asyncio.Event();release=asyncio.Event()
        async def call(data):entered.set();await release.wait();return '{"action":"keep"}'
        task=asyncio.create_task(self.f.check('group:12345',self.cursor,self.start,'1','草稿','',call,lambda:valid))
        await entered.wait();valid=False;release.set();self.assertEqual((await task)['reason'],'stale_work')
        task=asyncio.create_task(self.f.check('group:12345',self.cursor,self.start,'1','草稿','',call,lambda:True))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
    async def test_partial_delivery_cannot_replace_and_unknown_review_cannot_send(self):
        self.f.observe('group:12345','2','22222','补充')
        self.assertEqual((await self.run_check('replace',confirmed='已发第一条'))['reason'],'review_unavailable')
        self.assertEqual((await self.run_check('drop',confirmed='已发第一条'))['action'],'drop')
        async def invalid(data):return 'not JSON'
        result=await self.f.check('group:12345',self.cursor,self.start,'1','草稿','',invalid,lambda:True)
        self.assertEqual(result['action'],'drop')
        fallback=await self.f.check('group:12345',self.cursor,self.start,'1','草稿','',invalid,lambda:True,fallback=True)
        self.assertEqual(fallback['action'],'keep');self.assertTrue(fallback['quote_original'])
        partial=await self.f.check('group:12345',self.cursor,self.start,'1','草稿','已发',invalid,lambda:True,fallback=True)
        self.assertEqual(partial['action'],'drop')
    async def test_buffer_and_review_budget_are_bounded(self):
        for n in range(150):self.f.observe('group:'+str(10000+n),str(n),'11111','x'*2000)
        self.assertEqual(len(self.f.groups),128)
        for n in range(100):self.f.observe('group:12345',str(n),'11111','x'*2000)
        self.assertEqual(len(self.f.groups['group:12345']),32)
        self.assertLessEqual(len(self.f.changes('group:12345',0,self.start)),12)
        async def never(data):raise AssertionError('budget exhausted must not call model')
        result=await self.f.check('group:12345',0,self.start,'1','草稿','',never,lambda:True,timeout=0)
        self.assertEqual(result['reason'],'review_unavailable')
    def test_native_history_replaces_only_final_draft_preserving_tools(self):
        from astrbot.core.agent.message import Message
        messages=[Message(role='user',content='问题'),Message(role='assistant',content='中间步骤'),Message(role='assistant',content='过时草稿')]
        revised=history_override(messages,'新回复')
        self.assertEqual(len(revised),3);self.assertEqual(revised[-1].content,'新回复')
        self.assertEqual(messages[-1].content,'过时草稿')
        self.assertEqual(history_override(messages,''),messages[:-1])
        self.assertIs(history_override(messages,'不要覆盖后续新回复',original='之前的工具消息'),messages)

if __name__=='__main__':unittest.main()
