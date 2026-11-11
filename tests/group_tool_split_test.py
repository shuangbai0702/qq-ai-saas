"""Real group-tool dispatch with isolated receipts; no QQ or model calls."""
import asyncio,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parent))
import conversation_continuity_test as continuity
import group_actions
from bot_pipeline import Work


class GroupToolSplit(continuity.GroupActions):
    async def dispatch(self,text='第一段\n\n第二段',**kw):
        self.cfg['behavior']['splitMessages']=True
        return json.loads(await group_actions.send(self.event,self.cfg,self.db,'55555',text,'12345678',lambda:self.current,lambda *args:self.recorded.append(args),**kw))

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
        recovered=json.loads(group_actions.prior_receipt(self.db,group_actions.attempt_key(self.event)))
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
        result=json.loads(group_actions.prior_receipt(self.db,group_actions.attempt_key(self.event)))
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


if __name__=='__main__':unittest.main()
