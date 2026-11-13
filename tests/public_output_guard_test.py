"""Public envelope admission and real SDK/Journal regeneration, without QQ I/O."""
import asyncio,copy,json,sys,unittest,uuid
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
import activity_main_journal_test as fixture
import httpx
from public_output import inspect as inspect_public,HEADERS,Rejected,pending_tools,REGENERATE
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.platform.astr_message_event import current_message_event
from astrbot.core.platform.astrbot_message import MessageMember
from astrbot.core.message.components import Plain,Node
from astrbot.api.event import MessageChain
from aiocqhttp import CQHttp
from context_protocol import SOURCE,model_json

LEAK='正常开头\n\n'+HEADERS[0]+'\n'+json.dumps({'eventChanges':[['set',['authority'],{'identity':{'bot_id':'fixture'},'task':{'id':'invented'}}]]})

class Admission(unittest.TestCase):
    def test_protocol_and_split_nested_nodes(self):
        self.assertEqual(inspect_public(LEAK),'internal_event_envelope')
        node=NS(content=[Plain(LEAK[:38]),Plain(LEAK[38:])])
        self.assertTrue(inspect_public(NS(chain=[node])))
        self.assertTrue(inspect_public(HEADERS[1]+'\n'+json.dumps({'readonlyChanges':[['remove',['conversation_candidate']]],'meaning':'platform'})))

    def test_technical_examples_and_normal_json(self):
        for value in ('eventChanges 和 readonlyChanges 是什么？',json.dumps({'eventChanges':[['set',['authority'],{}]]}),
                      '```json\n'+LEAK+'\n```','> '+HEADERS[0]+'\n> '+json.dumps({'eventChanges':[['set',['policy'],'example']]}),
                      '引用 `'+HEADERS[0]+'` 这个标记。',HEADERS[0]+'\n'+json.dumps({'normal':'tool result'})):
            with self.subTest(value=value):self.assertIsNone(inspect_public(value))
        self.assertIsNone(inspect_public('示例 sk-example-only 和 Bearer TOKEN'))
        self.assertEqual(inspect_public('actual-private-secret',['actual-private-secret']),'runtime_credential')

    def test_pending_call_requires_original_tool_loop(self):
        history=[{'role':'assistant','tool_calls':[{'id':'a'}]}]
        self.assertTrue(pending_tools(history))
        self.assertFalse(pending_tools(history+[{'role':'tool','tool_call_id':'a','content':'rejected'}]))

class Regeneration(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.Activity.asyncSetUp
    asyncTearDown=fixture.Activity.asyncTearDown
    activate=fixture.Activity.activate

    async def scenario(self,answers,forward=False,pending=False):
        if not self.st.model_journals:
            activation=await self.activate()
            self.assertEqual(activation['status'],'completed',activation)
        self.wire_before=copy.deepcopy(self.st.model_journals['bot_shared'].last.wire)
        rounds=self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0]
        scope='private:12345678'
        identifier='question-'+uuid.uuid4().hex
        event=self.brain.make_event('qq',{'id':identifier},scope,'真实问题','90000000')
        event.message_obj.sender=MessageMember(user_id='12345678',nickname='Synthetic')
        event.message_obj.raw_message={'post_type':'message','message_type':'private','user_id':12345678,'self_id':90000000,'message_id':identifier}
        event.set_extra('_hosted_memory_epoch',self.st.reflection.epoch())
        event.set_extra('_hosted_internal_work',None);event.set_extra('_hosted_initiative',False)
        from context_foundation import read
        event.set_extra('_hosted_context',model_json({'memory_foundation':read(self.st,self.settings['qq']['config']['behavior'],scope,'12345678'),'state':{}}))
        work=self.st.pipeline.bind(event,self.settings['qq'])
        import shared_context
        shared_context.observe(self.st.db,event,self.settings['qq']['config'])
        self.sent=[];new_requests=[];self.guard_requests=new_requests
        async def handler(request):
            payload=json.loads(request.content);new_requests.append(payload)
            content=answers.pop(0)
            message={'role':'assistant','content':content}
            if pending:message['tool_calls']=[{'id':'pending-read','type':'function','function':{'name':'inspect_context','arguments':'{}'}}]
            return httpx.Response(200,json={'id':'guard','model':'fixture','usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110},'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if pending else 'stop'}]})
        self.provider.client._client._transport=httpx.MockTransport(handler)
        # The installed unified wrapper has captured the native send method;
        # replace its closure's transport only, retaining actual guard/planner.
        send=AiocqhttpMessageEvent.send
        async def transport(event,message):
            self.sent.append(copy.deepcopy(message))
            if forward:
                return await CQHttp.call_action(event.bot,'send_private_forward_msg',user_id=12345678,messages=[{'type':'node','data':{'content':[{'type':'text','data':{'text':message.chain[0].text}}]}}])
            self.st.pipeline.sending(work);self.st.pipeline.confirmed(work)
        closure=dict(zip(send.__code__.co_freevars,send.__closure__))
        if 'original_send' not in closure:send=closure['send'].cell_contents
        cell=dict(zip(send.__code__.co_freevars,send.__closure__))['original_send']
        old_transport=cell.cell_contents;cell.cell_contents=transport
        protocol_cell=dict(zip(CQHttp.call_action.__code__.co_freevars,CQHttp.call_action.__closure__))['action_original']
        old_protocol=protocol_cell.cell_contents
        async def protocol(self,action,**params):return {'message_id':123456}
        protocol_cell.cell_contents=protocol
        token=current_message_event.set(event)
        try:
            from astrbot.core.agent.tool import ToolSet,FunctionTool
            tools=ToolSet(tools=[FunctionTool(name='inspect_context',description='read only',parameters={'type':'object','properties':{}})]) if pending else None
            response=await self.provider.text_chat(system_prompt='Synthetic stable persona',contexts=[],func_tool=tools,prompt=SOURCE+model_json({'scope':scope,'sender_id':'12345678','event_id':work.event_id})+'\n真实问题')
            if pending:
                self.assertTrue(response.tools_call_name)
                self.assertTrue(event.get_extra('_hosted_model_tools_pending'))
            await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain(LEAK if pending else response.completion_text)]))
        finally:
            current_message_event.reset(token);cell.cell_contents=old_transport;protocol_cell.cell_contents=old_protocol
        for payload in new_requests[1:]:
            self.assertNotIn('tools',payload)
            self.assertEqual(payload['messages'][:len(self.wire_before)],self.wire_before)
            self.assertTrue(any(REGENERATE in str(m.get('content','')) for m in payload['messages']))
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],rounds)
        self.st.pipeline.finish(work,self.runtime['metrics'].setdefault('qq',{}))
        return event,new_requests

    async def test_same_journal_regeneration_and_bounded_failure(self):
        event,requests=await self.scenario([LEAK,'正常的新答复'])
        self.assertEqual(len(requests),2,{'retries':event.get_extra('_hosted_output_retries'),'status':event.get_extra('_hosted_pipeline').status})
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0].chain[0].text,'正常的新答复')
        self.assertEqual(event.get_extra('_hosted_output_retries'),1)

        event,requests=await self.scenario([LEAK,LEAK,LEAK])
        self.assertEqual(len(requests),3)
        self.assertEqual(self.sent,[])
        self.assertEqual(event.get_extra('_hosted_output_retries'),2)

        event,requests=await self.scenario(['正常合并转发'],forward=True)
        work=event.get_extra('_hosted_pipeline')
        self.assertEqual((work.attempted,work.confirmed),(1,1))
        self.assertEqual(event.get_extra('_hosted_protocol_receipt_count'),1)

        with self.assertRaises(Rejected):await self.scenario([LEAK],pending=True)
        self.assertEqual(len(self.guard_requests),1) # Original tool loop owns recovery.
        self.assertEqual(self.sent,[])

if __name__=='__main__':unittest.main()
