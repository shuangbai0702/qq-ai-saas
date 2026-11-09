"""Real storage/tool contracts with isolated protocol and model fixtures."""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from bot_pipeline import conversation_context
from memory_reflection import invalidate
from memory_scope import pending_messages
import group_actions,message_provenance as provenance


class MessageProvenance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.now=time.time()
        self.group='group:55555';self.owner='12345678';self.member='23456789';self.peer='34567890';self.self_id='900001'
        self.cfg={'behavior':{'ownerUin':self.owner,'memory':True}}
        self.extra={};self.sent=[]
        async def login():return {'user_id':int(self.self_id)}
        async def groups():return [{'group_id':55555}]
        async def member(**kw):return {'user_id':kw['user_id']}
        async def members(**kw):return [{'user_id':int(self.member),'card':'甲'},{'user_id':int(self.peer),'card':'乙'},{'user_id':45678901,'card':'丙'}]
        async def send(**kw):self.sent.append(kw);return {'message_id':101}
        self.event=NS(bot=NS(get_login_info=login,get_group_list=groups,get_group_member_info=member,get_group_member_list=members,send_group_msg=send),
                      message_obj=NS(message_id='origin-private',raw_message={}),get_group_id=lambda:'',get_sender_id=lambda:self.owner,get_self_id=lambda:self.self_id,
                      get_message_str=lambda:'private request must stay private',get_extra=lambda k,d=None:self.extra.get(k,d),set_extra=lambda k,v:self.extra.update({k:v}))
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def incoming(self,event='received',text='请多说一点',person=None,at=None,source=None,scope=None):
        self.store.reflection.incoming(scope or self.group,person or self.member,event,text,self.now if at is None else at,source=source)
    def confirmed(self,event='tool:origin-private',text='甲，你在催乙。',at=None,complete=True):
        self.store.participation.confirmed(self.group,text,self.now+1 if at is None else at,'',event,{'sender_id':self.self_id,'message_ids':['101'],'receipt':'confirmed','complete':complete})
    def test_protocol_capture_keeps_author_mentions_and_quote_separate(self):
        from astrbot.core.message.components import At,Reply,Plain
        event=NS(get_messages=lambda:[At(qq=self.peer),Plain('我是丙'),Reply(id='quoted',sender_id=self.peer,message_str='quoted text')],get_sender_id=lambda:self.member,get_sender_name=lambda:'甲')
        source=provenance.capture(event);self.incoming(source=source)
        item=pending_messages(self.db,self.group,self.member,self.now+1,cross_scope=False)[0]
        self.assertEqual(item['sender_id'],self.member);self.assertEqual(item['source']['sender_name'],'甲')
        self.assertEqual(item['source']['mentions'],[self.peer]);self.assertEqual(item['source']['reply_to']['sender_id'],self.peer)
        self.incoming(source={'sender_id':self.member,'sender_name':'fake replacement'})
        self.assertEqual(provenance.get(self.db,self.group,'received')['sender_name'],'甲')
    async def test_group_tool_binds_each_message_name_to_its_own_id(self):
        self.incoming();self.incoming('peer','看心情',self.peer,self.now+.1)
        result=json.loads(await group_actions.read_context(self.event,self.cfg,self.db,'55555',lambda:True))
        self.assertEqual([(x['sender_id'],x['sender_name']) for x in result['recent_received']],[(self.member,'甲'),(self.peer,'乙')])
        self.assertTrue(all(x['source_event'] for x in result['recent_received']))
        self.assertNotIn('丙',json.dumps(result['recent_received'],ensure_ascii=False))
    async def test_cross_session_send_visible_in_destination_without_private_request(self):
        self.incoming(scope='private:'+self.owner,person=self.owner,text='private request must stay private')
        def on_sent(scope,text,subject):
            receipt=self.extra['_hosted_tool_results'][-1]['receipt']
            self.store.participation.confirmed(scope,text,time.time(),subject,'tool:origin-private',{'sender_id':self.self_id,'message_ids':[receipt],'receipt':'confirmed','complete':True})
        result=json.loads(await group_actions.send(self.event,self.cfg,self.db,'55555','甲，你在催乙。','',lambda:True,on_sent))
        self.assertEqual(result['status'],'confirmed');self.assertEqual(len(self.sent),1)
        group=conversation_context(self.store,{'memory':True},self.group,self.peer,'',time.time()+1)['conversation_timeline']
        self.assertEqual(group[0]['role'],'self');self.assertEqual(group[0]['sender_id'],self.self_id)
        self.assertEqual(group[0]['message_ids'],['101']);self.assertNotIn('private request',json.dumps(group))
        await group_actions.send(self.event,self.cfg,self.db,'55555','another text','',lambda:True,on_sent)
        self.assertEqual(len(self.sent),1,'uncertain/confirmed actions must not be resent')
    def test_timeline_orders_actual_speakers_without_claiming_equal_text(self):
        self.incoming(source={'sender_id':self.member,'sender_name':'甲'})
        self.confirmed(text='像拿玩具逗猫。')
        self.incoming('peer','猫不理就是不理。',self.peer,self.now+2,{'sender_id':self.peer,'sender_name':'乙'})
        timeline=provenance.timeline(self.db,self.group,self.peer,self.now+3)
        self.assertEqual([x['role'] for x in timeline],['member','self','member'])
        self.assertEqual([x['sender_id'] for x in timeline],[self.member,self.self_id,self.peer])
        self.assertEqual(timeline[1]['text'],'像拿玩具逗猫。');self.assertEqual(timeline[2]['text'],'猫不理就是不理。')
    def test_restart_restores_provenance_and_one_logical_reply(self):
        self.incoming(source={'sender_id':self.member,'sender_name':'甲','mentions':[self.peer]})
        self.confirmed(at=self.now-.1);self.store.participation.outgoing(self.group,'甲，你在催乙。',self.now-.1,event='tool:origin-private')
        before=provenance.timeline(self.db,self.group,self.member,self.now+2)
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        self.assertEqual(provenance.timeline(self.db,self.group,self.member,self.now+2),before)
        turns=self.store.participation.context(self.group,now=self.now+2)['recent_own_turns']
        self.assertEqual(len(turns),1);self.assertEqual(turns[0]['source_event'],'tool:origin-private')
    def test_confirmed_prefix_never_invents_failed_suffix_or_counts_bubbles(self):
        self.confirmed(text='第一段',complete=False);self.confirmed(text='第一段\n第二段',complete=False)
        item=provenance.timeline(self.db,self.group,self.member,self.now+2)[0]
        self.assertEqual(item['text'],'第一段\n第二段');self.assertFalse(item['complete'])
        self.assertNotIn('第三段',item['text']);self.assertEqual(self.db.execute('SELECT count(*) FROM dialogue_feedback').fetchone()[0],1)
        self.assertEqual(self.store.participation.scopes.get(self.group,{}).get('sent',[]),[],'partial bubbles do not charge a completed reply')
        self.assertNotIn('rhythm',self.store.participation.context(self.group,now=self.now+2),'fatigue counters do not enter model context')
    def test_unknown_receipts_cannot_create_own_timeline_entries(self):
        self.store.participation.confirmed(self.group,'not confirmed',self.now,'','unknown',{'receipt':'unknown','message_ids':['101']})
        self.store.participation.confirmed(self.group,'no id',self.now,'','no-id',{'receipt':'confirmed','message_ids':[]})
        self.assertEqual(provenance.timeline(self.db,self.group,self.member,self.now+1),[])
    async def test_failed_protocol_response_with_id_is_not_confirmed(self):
        async def failed(**kw):return {'message_id':101,'retcode':100,'status':'failed'}
        self.event.bot.send_group_msg=failed
        result=await group_actions.send(self.event,self.cfg,self.db,'55555','test','',lambda:True,lambda *a:self.fail('invalid receipt committed'))
        self.assertIn('未确认',result);self.assertEqual(self.db.execute('SELECT state FROM group_message_attempts').fetchone()[0],'unknown')
    async def test_receipt_after_config_change_settles_only_original_attempt(self):
        current=True;entered=asyncio.Event();release=asyncio.Event();callbacks=[]
        async def delayed(**kw):entered.set();await release.wait();return {'message_id':101}
        self.event.bot.send_group_msg=delayed
        task=asyncio.create_task(group_actions.send(self.event,self.cfg,self.db,'55555','test','',lambda:current,lambda *a:callbacks.append(a)))
        await asyncio.wait_for(entered.wait(),3);current=False;release.set();result=await task
        self.assertIn('已确认',result);self.assertEqual(callbacks,[])
        self.assertEqual(self.db.execute('SELECT state,receipt FROM group_message_attempts').fetchone(),('confirmed','101'))
    async def test_summary_retains_sources_and_success_replaces_pending(self):
        for i in range(35):
            event='r'+str(i);at=self.now-20+i
            self.incoming(event,'问题'+str(i),at=at,source={'sender_id':self.member,'sender_name':'甲','mentions':[self.peer]})
            self.store.reflection.record(self.group,self.member,event,'问题'+str(i),'回答'+str(i),at+1)
        self.confirmed(at=self.now-18)
        captured=[]
        async def summarize(**kw):captured.append(json.loads(kw['prompt']));return NS(completion_text='真实有来源的摘要')
        self.store.reflection.maybe_start(self.group,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=summarize),lambda:True,{},contextvars.ContextVar('event',default=None))
        await self.store.reflection.task
        self.assertEqual(captured[0]['turns'][0]['source']['sender_name'],'甲');self.assertEqual(captured[0]['sent_messages'][0]['sender_id'],self.self_id)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_pending').fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_message_sources WHERE direction='incoming'").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_message_sources WHERE direction='outgoing'").fetchone()[0],1)
    async def test_forget_during_summary_does_not_restore_metadata_or_old_summary(self):
        for i in range(35):
            self.incoming(str(i),at=self.now-10+i,source={'sender_id':self.member,'sender_name':'甲'})
            self.store.reflection.record(self.group,self.member,str(i),'请多说一点','回答',self.now-9+i)
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(**kw):entered.set();await release.wait();return NS(completion_text='stale')
        self.store.reflection.maybe_start(self.group,{'memorySummary':True,'summaryRounds':35},NS(text_chat=delayed),lambda:True,{},contextvars.ContextVar('event',default=None))
        await asyncio.wait_for(entered.wait(),3);invalidate(self.db,self.group,self.member);self.db.commit();release.set();await self.store.reflection.task
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_message_sources WHERE direction='incoming'").fetchone()[0],0)
    def test_disabled_summary_memory_and_other_bot_are_isolated(self):
        self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.group,'old summary','[]',self.now));self.db.commit()
        ctx=conversation_context(self.store,{'memory':True,'memorySummary':False},self.group,self.member,'old',self.now+1)
        self.assertEqual(ctx['memory_foundation']['conversation_summaries'],[])
        self.assertNotIn('conversation_summaries',ctx['state'])
        ctx=conversation_context(self.store,{'memory':False},self.group,self.member,'',self.now+1,state={'facts':['stale'],'conversation_summaries':['stale']})
        self.assertEqual(ctx['memory_foundation']['facts'],[]);self.assertEqual(ctx['memory_foundation']['conversation_summaries'],[]);self.assertEqual(ctx['conversation_timeline'],[])
        self.assertNotIn('facts',ctx['state']);self.assertNotIn('conversation_summaries',ctx['state'])
        self.confirmed();other=BehaviorStore(Path(self.tmp.name)/'other.sqlite')
        try:self.assertEqual(provenance.timeline(other.db,self.group,self.member,self.now+2),[])
        finally:other.close()
    def test_bounded_retention_and_private_destination_guard(self):
        for i in range(35):self.incoming(str(i),'z'*500,at=self.now+i,source={'sender_id':self.member,'sender_name':'甲'})
        self.confirmed(at=self.now+34)
        timeline=provenance.timeline(self.db,self.group,self.member,self.now+36)
        self.assertLessEqual(len(timeline),16);self.assertTrue(any(x['role']=='self' for x in timeline))
        self.assertEqual(provenance.timeline(self.db,'private:'+self.owner,self.member,self.now+36),[])
        self.db.execute('DELETE FROM dialogue_feedback');self.db.execute('DELETE FROM memory_pending');self.db.commit()
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_message_sources').fetchone()[0],0)


if __name__=='__main__':unittest.main(verbosity=2)
