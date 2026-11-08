"""Real pinned SDK types; no network or paid model requests."""
import asyncio,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.provider.entities import LLMResponse
from mcp.types import CallToolResult,ImageContent,TextContent
from behavior import sanitize_new_response
from tool_outcome import structured,project,writes
from output_clean import memory_projection
from group_freshness import response_coverage

class ResponseBoundary(unittest.TestCase):
    def test_protocol_no_change_check_retains_declared_coverage(self):
        previous=response_coverage([],{'action':'replace','checked':True,'responded_events':['event-b']})
        self.assertEqual(response_coverage(previous,{'action':'keep','checked':False}),['event-b'])
        self.assertEqual(response_coverage(previous,{'action':'keep','checked':True,'responded_events':['event-c']}),['event-b','event-c'])
        self.assertEqual(response_coverage(previous,{'action':'replace','checked':True,'responded_events':['event-c']}),['event-c'])
        self.assertEqual(response_coverage(previous,{'action':'drop'}),[])
    def test_memory_view_does_not_rewrite_evidence_or_human_quotes(self):
        raw={'turns':[{'user':'<think>用户在讨论标签</think>','bot':'<thinking>内部</thinking>真实回答'}],
             'observed_requests':[{'text':'<think>真实原话</think>','native_reply':'<thinking内部','confirmed_reply':'正常回答','tools':[{'result':'原始证据'}]}]}
        view=memory_projection(raw)
        self.assertEqual(view['turns'][0]['bot'],'真实回答')
        self.assertEqual(view['turns'][0]['user'],raw['turns'][0]['user'])
        self.assertEqual(view['observed_requests'][0]['native_reply'],'')
        self.assertEqual(raw['observed_requests'][0]['native_reply'],'<thinking内部')
        self.assertEqual(view['observed_requests'][0]['tools'],raw['observed_requests'][0]['tools'])
    def test_new_response_and_tool_step(self):
        response=LLMResponse(role='assistant',completion_text='<thinking群里另一个人</thinking>正常回复',reasoning_content='provider-private',reasoning_signature='protocol-signature')
        sanitize_new_response(response);self.assertEqual(response.completion_text,'正常回复')
        self.assertEqual(response.reasoning_content,'provider-private');self.assertEqual(response.reasoning_signature,'protocol-signature')
        response=LLMResponse(role='assistant',completion_text='内部执行说明',tools_call_name=['a'],tools_call_args=[{'x':1}],tools_call_ids=['call1'])
        sanitize_new_response(response)
        self.assertEqual(response.completion_text,'');self.assertEqual(response.tools_call_args,[{'x':1}]);self.assertEqual(response.tools_call_ids,['call1'])
    def test_malformed_incident_is_withheld(self):
        response=LLMResponse(role='assistant',completion_text='<thinking最新消息 group_freshness suggested_text')
        sanitize_new_response(response);self.assertEqual(response.completion_text,'')

class NativeResults(unittest.IsolatedAsyncioTestCase):
    async def test_installed_stream_boundary_blocks_split_tags_and_closes_provider(self):
        from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
        from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
        from astrbot.core.agent.response import AgentResponse,AgentResponseData
        from astrbot.core.message.message_event_result import MessageChain
        from behavior import install_runtime
        original=ProviderOpenAIOfficial.text_chat_stream
        original_step=ToolLoopAgentRunner.step
        async def private_and_public(runner):
            yield AgentResponse(type='llm_result',data=AgentResponseData(chain=MessageChain(type='reasoning').message('provider-private')))
            yield AgentResponse(type='llm_result',data=AgentResponseData(chain=MessageChain().message('真实回复')))
        ToolLoopAgentRunner.step=private_and_public
        closed=[];mode=['complete']
        async def stream(provider,prompt,system_prompt='',contexts=None,**kwargs):
            try:
                for piece in ('<thi','nking内部推理','</thinking>真实回复'):
                    yield LLMResponse(role='assistant',completion_text=piece,is_chunk=True)
                if mode[0]!='incomplete':
                    yield LLMResponse(role='assistant',completion_text='<thinking内部推理</thinking>真实回复',is_chunk=False)
                    await asyncio.sleep(0)
            finally:closed.append(mode[0])
        ProviderOpenAIOfficial.text_chat_stream=stream
        core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={}))
        runtime=install_runtime(core,{})
        provider=ProviderOpenAIOfficial.__new__(ProviderOpenAIOfficial)
        try:
            public=[r async for r in ToolLoopAgentRunner.__new__(ToolLoopAgentRunner).step()]
            self.assertEqual([r.data['chain'].get_plain_text() for r in public],['真实回复'])
            result=[r async for r in provider.text_chat_stream(prompt='offline')]
            self.assertEqual([r.completion_text for r in result],['真实回复']);self.assertEqual(closed,['complete'])
            mode[0]='incomplete'
            with self.assertRaisesRegex(RuntimeError,'incomplete_model_stream'):
                _=[r async for r in provider.text_chat_stream(prompt='offline')]
            mode[0]='consumer_closed'
            generator=provider.text_chat_stream(prompt='offline')
            self.assertEqual((await anext(generator)).completion_text,'真实回复')
            await generator.aclose();self.assertEqual(closed[-1],'consumer_closed')
        finally:
            runtime['close']();ProviderOpenAIOfficial.text_chat_stream=original;ToolLoopAgentRunner.step=original_step

    async def test_multimodal_result_survives_wrapper(self):
        original=CallToolResult(content=[TextContent(type='text',text='{"status":"prepared","handle":"safe"}'),ImageContent(type='image',data='YWJj',mimeType='image/png')])
        class Media(FunctionTool):
            async def call(self,context,**kwargs):return original
        result=await structured(Media(name='media',description='read',parameters={})).call(NS())
        self.assertIsInstance(result,CallToolResult)
        self.assertEqual(result.content[1],original.content[1]);self.assertEqual(original.content[0].text,'{"status":"prepared","handle":"safe"}')
        self.assertEqual(json.loads(result.content[0].text)['receipt']['status'],'not_applicable')
        self.assertEqual(result.structuredContent['data']['handle'],'safe')
    async def test_real_native_runner_keeps_protocol_reasoning_private_and_media_usable(self):
        from behavior import install_runtime
        from native_internal import run,RunControl
        from astrbot.core.provider.entities import TokenUsage
        core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={}))
        runtime=install_runtime(core,{})
        calls=[]
        class Media(FunctionTool):
            async def call(self,context):
                return CallToolResult(content=[TextContent(type='text',text='{"status":"prepared","handle":"safe"}'),ImageContent(type='image',data='YWJj',mimeType='image/png')])
        class Provider:
            provider_config={'id':'offline','max_context_tokens':0}
            async def text_chat(self,**kwargs):
                calls.append(kwargs)
                if len(calls)==1:return LLMResponse(role='assistant',completion_text='<thinking内部执行说明',reasoning_content='private-protocol-reasoning',tools_call_name=['media'],tools_call_args=[{}],tools_call_ids=['call-1'],usage=TokenUsage(input_other=10,output=10))
                return LLMResponse(role='assistant',completion_text='<think>不要公开</think>看到了',usage=TokenUsage(input_other=10,output=10))
        try:
            control=RunControl(model_calls=2,tool_calls=1,tokens=24000,seconds=10,current=lambda:True)
            result=await run(Provider(),control,'offline','system',tools=[structured(Media(name='media',description='read',parameters={'type':'object','properties':{}}))])
            self.assertEqual(result['status'],'completed');self.assertEqual(result['text'],'看到了')
            self.assertEqual(result['usage']['modelCalls'],2);self.assertEqual(result['usage']['toolCalls'],1)
            transcript=json.dumps(calls[1],ensure_ascii=False,default=str)
            self.assertNotIn('内部执行说明',transcript);self.assertIn('private-protocol-reasoning',transcript)
            self.assertIn('image/png',transcript);self.assertIn('safe',transcript)
        finally:runtime['close']()
    async def test_business_failure_and_write_uncertainty(self):
        for value in ({'queued':False},{'ok':False},{'available':False},{'status':'invalid_source'}):
            self.assertEqual(project(value)['receipt']['status'],'rejected')
        class Mixed(FunctionTool):
            @property
            def hosted_write_operations(self):return frozenset({'save'})
            async def call(self,context,**kwargs):raise TypeError('do not expose request secrets')
        tool=structured(Mixed(name='mixed',description='mixed',parameters={}))
        self.assertFalse(writes(tool,{'operation':'list'}));self.assertTrue(writes(tool,{'operation':'save'}))
        self.assertEqual(json.loads(await tool.call(NS(),operation='save'))['receipt']['status'],'unknown')
        self.assertEqual(json.loads(await tool.call(NS(),operation='list'))['receipt']['status'],'rejected')
    async def test_mcp_error_preserves_original_confirmed_or_partial_receipt(self):
        for status in ('confirmed','partial','unknown','rejected'):
            value=CallToolResult(isError=True,content=[TextContent(type='text',text='controlled')],structuredContent={'receipt':{'status':status,'message_id':'actual-receipt'}})
            class Media(FunctionTool):
                @property
                def hosted_effect(self):return True
                async def call(self,context,**kwargs):return value
            result=await structured(Media(name='media',description='controlled',parameters={})).call(NS())
            self.assertEqual(result.structuredContent['execution'],'failed')
            self.assertEqual(result.structuredContent['receipt'],value.structuredContent['receipt'])
    async def test_cancellation_is_not_laundered_as_failure(self):
        from native_internal import WorkStopped,WorkBudgetExceeded
        class Cancel(FunctionTool):
            async def call(self,context,**kwargs):raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await structured(Cancel(name='cancel',description='cancel',parameters={})).call(NS())
        for exception in (WorkStopped,WorkBudgetExceeded):
            class Stop(FunctionTool):
                async def call(self,context,**kwargs):raise exception('must terminate the owning work')
            with self.assertRaises(exception):await structured(Stop(name='stop',description='stop',parameters={})).call(NS())

if __name__=='__main__':unittest.main()
