"""Real group-tool dispatch with isolated receipts; no QQ or model calls."""
import asyncio,hashlib,json,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parent))
import conversation_continuity_test as continuity
import group_actions
from bot_pipeline import Work


class GroupToolSplit(continuity.GroupActions):
    def setUp(self):
        super().setUp()
        # Production executes this tool only under a real tool-call identity.
        token=group_actions.tool_action_id.set('split-test-call')
        self.addCleanup(group_actions.tool_action_id.reset,token)
    async def dispatch(self,text='第一段\n\n第二段',**kw):
        self.cfg['behavior']['splitMessages']=True
        return json.loads(await group_actions.send(self.event,self.cfg,self.db,'55555',text,'12345678',lambda:self.current,lambda *args:self.recorded.append(args),**kw))

    async def test_long_transport_split_preserves_full_text_and_receipts(self):
        text='长回复😀'*800
        result=await self.dispatch(text)
        parts=[x['message'][-1]['data']['text'] for x in self.sent]
        self.assertEqual(''.join(parts),text)
        self.assertTrue(all(len(x.encode('utf-16-le'))//2<=1500 for x in parts))
        self.assertEqual(result['confirmed_count'],len(parts))
        await self.dispatch(text);self.assertEqual(len(self.sent),len(parts))

    async def test_paragraphs_split_with_first_mention_and_cumulative_confirmation(self):
        result=await self.dispatch()
        self.assertEqual(result['confirmed_count'],2)
        self.assertEqual([x['message'][-1]['data']['text'] for x in self.sent],['第一段','第二段'])
        self.assertEqual(self.sent[0]['message'][0]['type'],'at')
        self.assertEqual(self.sent[1]['message'][0]['type'],'text')
        self.assertEqual(self.recorded[-1][1],'第一段\n\n第二段')
        self.assertEqual(len(self.recorded),1)
        self.assertEqual(self.extra['_hosted_tool_results'][-1]['receipts'],['123','123'])
        await self.dispatch();self.assertEqual(len(self.sent),2)

    async def test_single_newline_code_and_lists_preserve_one_message(self):
        for index,text in enumerate(['第一句。\n第二句。','```python\na=1\n\nb=2\n```','- 第一项\n\n- 第二项']):
            self.event.message_obj.message_id=str(index)
            before=len(self.sent);await self.dispatch(text)
            self.assertEqual(len(self.sent)-before,1)

    async def test_split_limit_keeps_all_text(self):
        result=await self.dispatch('\n\n'.join('段'+str(i) for i in range(6)))
        self.assertEqual(result['planned_count'],4)
        self.assertEqual(self.sent[-1]['message'][-1]['data']['text'],'段3\n\n段4\n\n段5')

    async def test_disabled_setting_preserves_one_bubble(self):
        result=json.loads(await group_actions.send(self.event,self.cfg,self.db,'55555','一\n\n二','',lambda:True,lambda *a:None))
        self.assertEqual(result['planned_count'],1);self.assertEqual(len(self.sent),1)

    async def test_unknown_second_bubble_preserves_prefix_after_recovery_no_replay(self):
        async def send(**kw):
            self.sent.append(kw)
            if len(self.sent)==2:raise TimeoutError()
            return {'message_id':101}
        self.event.bot.send_group_msg=send
        result=await self.dispatch()
        self.assertEqual(result['receipt']['status'],'partial')
        self.assertEqual(result['message_ids'],['101'])
        self.assertEqual(result['remaining_status'],'unknown')
        self.assertEqual(self.recorded,[('group:55555','第一段','12345678')])
        self.assertFalse(self.extra['_hosted_tool_results'][-1]['complete'])
        # Read the persisted recovery path, not an in-memory result cache.
        recovered=json.loads(group_actions.prior_receipt(self.db,group_actions.attempt_key(self.event)+':action:'+hashlib.sha256(b'split-test-call').hexdigest()))
        self.assertEqual(recovered['message_ids'],['101'])
        self.assertEqual(recovered['remaining_status'],'unknown')
        await self.dispatch();self.assertEqual(len(self.sent),2)
        self.assertTrue(group_actions.owns_reply(self.db,self.event,'group:55555'))

    async def test_stale_after_first_receipt_stops_tail_and_no_new_state_write(self):
        async def send(**kw):
            self.sent.append(kw);self.current=False;return {'message_id':101}
        self.event.bot.send_group_msg=send
        result=await self.dispatch()
        self.assertEqual(result['confirmed_count'],1)
        self.assertFalse(result['state_committed'])
        self.assertEqual(result['remaining_status'],'not_sent')
        self.assertEqual(self.recorded,[]);self.assertEqual(len(self.sent),1)

    async def test_cancel_in_second_send_retains_first_receipt_and_unknown_tail(self):
        entered=asyncio.Event();wait=asyncio.Event()
        async def send(**kw):
            self.sent.append(kw)
            if len(self.sent)==2:entered.set();await wait.wait()
            return {'message_id':101}
        self.event.bot.send_group_msg=send
        task=asyncio.create_task(self.dispatch())
        await asyncio.wait_for(entered.wait(),2);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        result=json.loads(group_actions.prior_receipt(self.db,group_actions.attempt_key(self.event)+':action:'+hashlib.sha256(b'split-test-call').hexdigest()))
        self.assertEqual(result['message_ids'],['101']);self.assertEqual(result['remaining_status'],'unknown')
        await self.dispatch();self.assertEqual(len(self.sent),2)

    async def test_freshness_drop_does_not_resend_confirmed_prefix(self):
        async def review(scope,text,confirmed=''):
            if self.sent:self.assertEqual(confirmed,'第一段')
            return {'action':'keep' if not self.sent else 'drop'}
        result=await self.dispatch(review=review)
        self.assertEqual(result['confirmed_count'],1)
        self.assertEqual(result['remaining_status'],'not_sent')
        self.assertEqual(len(self.sent),1)

    async def test_image_only_on_last_bubble(self):
        self.extra['_hosted_tool_sticker']={'id':'fixture','bytes':b'image'}
        calls=[]
        social=NS(image_status=lambda *a,**k:{'reason':'available'},reserve_image=lambda *a:calls.append('reserve'),image_sent=lambda *a:calls.append('sent'))
        await self.dispatch(include_prepared_image=True,social=social)
        self.assertEqual([[x['type'] for x in s['message']] for s in self.sent],[['at','text'],['text','image']])
        self.assertEqual(calls,['reserve','sent'])

    async def test_matching_work_counts_each_protocol_receipt(self):
        work=Work('bot','group:55555','12345678','90000000','real-request','message',0,{})
        self.extra['_hosted_pipeline']=work
        await self.dispatch()
        self.assertEqual((work.planned_messages,work.attempted,work.confirmed,work.confirmed_messages),(2,2,2,2))
        self.assertEqual(work.confirmed_reply_parts,['第一段','第二段'])


class PrivateConversationSend(continuity.GroupActions):
    def setUp(self):
        super().setUp()
        self.target='76543210'
        self.friends=[{'user_id':int(self.target)}]
        self.work=Work('bot','private:12345678','12345678','90000000','real-request','message',0,{})
        self.extra['_hosted_pipeline']=self.work
        token=group_actions.tool_action_id.set('private-send-call')
        self.addCleanup(group_actions.tool_action_id.reset,token)

        async def friends():return self.friends
        async def send(**kwargs):
            self.sent.append(kwargs)
            return {'message_id':201}
        self.event.bot.get_friend_list=friends
        self.event.bot.send_private_msg=send

    async def call_private(self,**changes):
        tool=group_actions.tools(self.event,self.cfg,self.db,lambda:self.current,lambda *args:self.recorded.append(args))[-1]
        context=NS(context=NS(event=self.event))
        return json.loads(await tool.call(context,**{'private_user_id':self.target,'text':'吃饭啦',**changes}))

    async def test_private_send_from_group_uses_verified_friend_and_same_receipt_chain(self):
        self.event.get_group_id=lambda:'55555'
        self.work.scope='group:55555'
        result=await self.call_private()
        self.assertEqual(result['receipt']['status'],'confirmed')
        self.assertEqual(result['scope'],'private:'+self.target)
        self.assertEqual(self.sent,[{'user_id':int(self.target),'message':[{'type':'text','data':{'text':'吃饭啦'}}]}])
        self.assertEqual(self.work.confirmed,0)
        self.assertEqual(self.recorded,[('private:'+self.target,'吃饭啦','')])
        self.assertEqual((await self.call_private())['code'],'already_attempted')
        self.assertEqual(len(self.sent),1)
        self.assertFalse(group_actions.owns_reply(self.db,self.event,self.work.scope))

    async def test_owner_private_send_uses_same_split_tool_during_outreach_cooldown(self):
        from initiative import initialize, paused
        initialize(self.db)
        scope='private:'+self.target
        self.db.execute('INSERT INTO initiative_pauses VALUES(?,?)',(scope,time.time()+3600))
        self.db.commit()
        self.cfg['behavior']['splitMessages']=True
        result=await self.call_private(text='第一段\n\n第二段')
        self.assertEqual(result['receipt']['status'],'confirmed')
        self.assertEqual(result['message_ids'],['201','201'])
        self.assertEqual([part['message'][0]['data']['text'] for part in self.sent],['第一段','第二段'])
        self.assertTrue(paused(self.db,scope,time.time()))
        self.assertEqual((await self.call_private(text='第一段\n\n第二段'))['code'],'already_attempted')
        self.assertEqual(len(self.sent),2)

    async def test_ambiguous_unknown_and_mismatched_sources_never_dispatch(self):
        self.assertEqual((await self.call_private(group_id='55555'))['code'],'tool_preflight_rejected')
        self.friends=[]
        self.assertEqual((await self.call_private())['receipt']['status'],'rejected')
        self.friends=[{'user_id':int(self.target)}]
        self.work.scope='private:88888'
        self.assertEqual((await self.call_private())['receipt']['status'],'rejected')
        self.work.scope='private:12345678'
        self.event.get_sender_id=lambda:'88888'
        self.assertEqual((await self.call_private())['receipt']['status'],'rejected')
        self.assertEqual(self.sent,[])

    async def test_private_unknown_never_replays_and_keeps_sources_separate(self):
        async def timeout(**kwargs):
            self.sent.append(kwargs)
            raise TimeoutError()
        self.event.bot.send_private_msg=timeout
        result=await self.call_private()
        self.assertEqual(result['receipt']['status'],'unknown')
        retry=await self.call_private()
        self.assertEqual(retry['receipt']['status'],'unknown')
        self.assertEqual(retry['code'],'already_attempted')
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.recorded,[])
        self.assertFalse(group_actions.owns_reply(self.db,self.event,self.work.scope))


if __name__=='__main__':unittest.main()
