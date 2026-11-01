import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from memory_reflection import invalidate
import group_actions


class Continuity(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite';self.store=BehaviorStore(self.path)
        self.db=self.store.db;self.person='12345678';self.scope='group:55555';self.private='private:'+self.person;self.now=time.time()
        self.event_var=contextvars.ContextVar('event',default=None)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def pending(self,event='unanswered',person=None,scope=None):
        self.store.reflection.incoming(scope or self.scope,person or self.person,event,'刚在群里开头了',self.now)
    def seed(self):
        for i in range(5):self.store.reflection.record(self.scope,self.person,'answered'+str(i),'真实问题'+str(i),'真实回复'+str(i),self.now+i+1)
    async def summarize(self,chat,current=lambda:True):
        self.store.reflection.maybe_start(self.scope,{'memorySummary':True,'summaryRounds':5},NS(text_chat=chat),current,{},self.event_var)
        await self.store.reflection.task
    def test_unanswered_group_visible_in_private_with_real_source_and_time(self):
        self.pending()
        recent=self.store.reflection.recent(self.private,self.person,now=self.now+1)
        self.assertEqual(recent[0]['source_scope'],self.scope);self.assertEqual(recent[0]['sender_id'],self.person)
        self.assertEqual(recent[0]['user'],'刚在群里开头了');self.assertEqual(recent[0]['bot'],'');self.assertEqual(recent[0]['age_seconds'],1)
        self.assertEqual(self.store.reflection.recent(self.scope,self.person),[],'native current-session history is not injected twice')
    def test_other_person_private_bot_and_invalid_attribution_isolated(self):
        self.pending();self.pending('peer','87654321','private:87654321')
        self.pending('invalid',self.person,'private:87654321')
        self.assertEqual(self.store.reflection.recent('private:87654321','87654321'),[])
        self.assertEqual(len(self.store.reflection.recent(self.private,self.person)),1)
        other=BehaviorStore(Path(self.tmp.name)/'peer.sqlite')
        try:self.assertEqual(other.reflection.recent(self.private,self.person),[])
        finally:other.close()
    def test_pending_survives_restart_and_is_bounded(self):
        self.pending();self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        self.assertEqual(len(self.store.reflection.recent(self.private,self.person)),1)
        for i in range(1050):self.store.reflection.incoming(self.scope,self.person,str(i),'x'*800,self.now+2)
        self.assertLessEqual(self.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0],1000)
        recent=self.store.reflection.recent(self.private,self.person,now=self.now+3)
        self.assertLessEqual(sum(len(r['user'])+len(r['bot']) for r in recent),3000)
    async def test_successful_summary_atomically_replaces_only_covered_pending(self):
        self.pending();self.seed();calls=[]
        async def chat(**kw):calls.append(kw);return NS(completion_text='真实群聊摘要')
        await self.summarize(chat)
        payload=json.loads(calls[0]['prompt']);turns=payload['turns'] if isinstance(payload,dict) else payload
        self.assertIn('unanswered',{r['event'] for r in turns})
        self.assertEqual(self.store.reflection.recent(self.private,self.person),[])
        self.assertTrue(self.store.reflection.recall(self.private,'真实群聊',subject=self.person))
        self.assertEqual(len(calls),1)
    async def test_new_messages_and_updated_answer_while_compressing_are_retained(self):
        self.pending();self.seed();entered=asyncio.Event();release=asyncio.Event()
        async def chat(**kw):entered.set();await release.wait();return NS(completion_text='摘要只覆盖旧版本')
        task=asyncio.create_task(self.summarize(chat));await entered.wait()
        self.store.reflection.record(self.scope,self.person,'unanswered','刚在群里开头了','后来真实回复',self.now+6)
        self.pending('newer');release.set();await task
        records=self.store.reflection.recent(self.private,self.person,now=self.now+7)
        self.assertEqual({r['source_event'] for r in records},{'unanswered','newer'})
        self.assertEqual(next(r for r in records if r['source_event']=='unanswered')['bot'],'后来真实回复')
    async def test_failure_and_stale_summary_do_not_erase_pending(self):
        self.pending();self.seed()
        async def failed(**kw):raise TimeoutError()
        await self.summarize(failed);self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0],6)
        async def chat(**kw):return NS(completion_text='迟到摘要')
        await self.summarize(chat,lambda:False)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0],6)
    def test_ambient_messages_do_not_create_more_summary_calls_and_forget_clears(self):
        for i in range(35):self.pending(str(i))
        self.store.reflection.maybe_start(self.scope,{'memorySummary':True,'summaryRounds':5},NS(text_chat=None),lambda:True,{},self.event_var)
        self.assertIsNone(self.store.reflection.task)
        invalidate(self.db,self.private,self.person);self.db.commit()
        self.assertEqual(self.store.reflection.recent(self.private,self.person),[])


class GroupActions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db
        self.cfg={'groups':['55555'],'behavior':{'ownerUin':'12345678'}};self.current=True;self.sent=[];self.recorded=[];self.identity='90000000';self.member=True
        async def login():return {'user_id':self.identity}
        async def groups():return [{'group_id':55555,'group_name':'真实群'}]
        async def member(**kw):return {'user_id':kw['user_id'] if self.member else 0}
        async def send(**kw):self.sent.append(kw);return {'message_id':123}
        self.event=NS(get_sender_id=lambda:'12345678',get_self_id=lambda:'90000000',get_group_id=lambda:'',get_extra=lambda k,d=None:d,message_obj=NS(message_id='real-request'),bot=NS(get_login_info=login,get_group_list=groups,get_group_member_info=member,send_group_msg=send))
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def send(self,group='55555',mention='12345678'):
        return await group_actions.send(self.event,self.cfg,self.db,group,'来接着聊',mention,lambda:self.current,lambda *args:self.recorded.append(args))
    async def test_owner_send_has_real_at_and_one_attempt_per_source_event(self):
        self.assertIn('真实艾特',await self.send());self.assertEqual(self.sent[0]['message'][0],{'type':'at','data':{'qq':'12345678'}})
        await self.send();self.assertEqual(len(self.sent),1);self.assertEqual(len(self.recorded),1)
    async def test_identity_group_owner_membership_and_nonowner_block_before_send(self):
        self.store.reflection.incoming('group:55555','12345678','cached','关闭记忆后不应读取这条',time.time())
        self.cfg['behavior']['memory']=False
        async def member_list(**kw):return [{'user_id':12345678,'nickname':'真实成员'}]
        self.event.bot.get_group_member_list=member_list
        context=json.loads(await group_actions.read_context(self.event,self.cfg,self.db,'55555',lambda:self.current))
        self.assertEqual(context['recent_received'],[]);self.assertNotIn('关闭记忆后不应读取这条',json.dumps(context,ensure_ascii=False))
        self.assertIn('没有向群发送',await self.send('66666'))
        self.member=False;self.assertIn('没有向群发送',await self.send());self.member=True
        self.identity='90000001';self.assertIn('没有向群发送',await self.send());self.identity='90000000'
        self.event.get_sender_id=lambda:'87654321';self.assertIn('拒绝',await self.send());self.assertEqual(self.sent,[])
    async def test_config_change_during_membership_check_cannot_dispatch(self):
        async def member(**kw):self.current=False;return {'user_id':kw['user_id']}
        self.event.bot.get_group_member_info=member;await self.send();self.assertEqual(self.sent,[])
    async def test_uncertain_send_no_retry_and_no_fake_success(self):
        async def uncertain(**kw):self.sent.append(kw);raise TimeoutError()
        self.event.bot.send_group_msg=uncertain
        self.assertIn('未确认',await self.send());await self.send()
        self.assertEqual(len(self.sent),1);self.assertEqual(self.recorded,[])
    async def test_late_receipt_cannot_write_to_replaced_store(self):
        async def send(**kw):self.current=False;return {'message_id':123}
        self.event.bot.send_group_msg=send
        self.assertIn('过期',await self.send());self.assertEqual(self.recorded,[])
        self.assertEqual(self.db.execute('SELECT state FROM group_message_attempts').fetchone()[0],'attempted')
    async def test_refused_member_not_contacted(self):
        self.db.execute("INSERT INTO companion_members(scope,subject,seen,refused) VALUES('group:55555','87654321',?,1)",(time.time(),));self.db.commit()
        self.assertIn('拒绝主动联系',await self.send(mention='87654321'));self.assertEqual(self.sent,[])


if __name__=='__main__':unittest.main(verbosity=2)
