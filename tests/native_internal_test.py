"""Pinned native runner contracts; provider is deterministic, not a real model."""
import asyncio,json,os,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from native_internal import RunControl,run,BoundedProvider
from astrbot.core.provider.entities import LLMResponse,TokenUsage
from astrbot.core.agent.tool import FunctionTool


class Provider:
    provider_config={'id':'native-contract','max_context_tokens':0}
    def __init__(self,reply):self.reply=reply;self.calls=0
    async def text_chat(self,**kw):self.calls+=1;return await self.reply(self.calls,kw)


class NativeTests(unittest.IsolatedAsyncioTestCase):
    def control(self,**kw):return RunControl(**{'model_calls':4,'tool_calls':3,'tokens':24000,'seconds':10,'current':lambda:True,**kw})
    async def test_unknown_tool_and_invalid_arguments_return_feedback_then_recover(self):
        observed=[]
        class Read(FunctionTool):
            async def call(self,context,**arguments):observed.append(arguments);return 'verified-result'
        tool=Read(name='bilibili_video_info',description='real registered fixture',parameters={'type':'object','properties':{'page':{'type':'integer','minimum':1}},'required':['page'],'additionalProperties':False})
        async def reply(n,kw):
            if n==1:return LLMResponse(role='assistant',tools_call_name=['公开视频画面核查'],tools_call_args=[{}],tools_call_ids=['unknown'])
            if n==2:
                self.assertIn('Available tools are: bilibili_video_info',str(kw))
                return LLMResponse(role='assistant',tools_call_name=['bilibili_video_info'],tools_call_args=[{'page':'wrong'}],tools_call_ids=['invalid'])
            if n==3:
                self.assertIn('invalid_tool_arguments',str(kw))
                return LLMResponse(role='assistant',tools_call_name=['bilibili_video_info'],tools_call_args=[{'page':1}],tools_call_ids=['valid'])
            self.assertIn('verified-result',str(kw));return LLMResponse(role='assistant',completion_text='recovered')
        result=await run(Provider(reply),self.control(),'read','system',tools=[tool])
        self.assertEqual(result['status'],'completed');self.assertEqual(observed,[{'page':1}]);self.assertEqual(result['usage']['modelCalls'],4)

    async def test_cycle_reserve_precedes_provider_and_settles_actual_usage(self):
        events=[]
        async def budget(request,phase,tokens):events.append((request,phase,tokens))
        async def reply(n,kw):
            self.assertEqual(events[-1][1],'reserve')
            return LLMResponse(role='assistant',completion_text='done',usage=TokenUsage(input_other=10,output=3))
        await BoundedProvider(Provider(reply),self.control(model_budget=budget)).text_chat(prompt='fixture')
        self.assertEqual([v[1] for v in events],['reserve','settled']);self.assertEqual(events[-1][2],13);self.assertEqual(events[0][0],events[1][0])

    async def test_cycle_failures_release_only_proven_not_sent(self):
        for stage,phase in [('request_not_sent','released'),(None,'unknown')]:
            events=[]
            async def budget(request,state,tokens):events.append(state)
            async def reply(n,kw):
                error=RuntimeError('fixture');error._fishbot_submission_stage=stage;raise error
            with self.assertRaises(RuntimeError):await BoundedProvider(Provider(reply),self.control(model_budget=budget)).text_chat(prompt='fixture')
            self.assertEqual(events,['reserve',phase])

    async def test_failed_cycle_reserve_never_calls_provider(self):
        async def budget(*args):raise RuntimeError('budget exhausted')
        provider=Provider(None)
        with self.assertRaises(RuntimeError):await BoundedProvider(provider,self.control(model_budget=budget)).text_chat(prompt='fixture')
        self.assertEqual(provider.calls,0)
    async def test_diagnostics_correlate_usage_without_logging_prompt_or_changing_request(self):
        seen=[]
        async def reply(n,kw):
            seen.append(kw)
            return LLMResponse(role='assistant',completion_text='done',usage=TokenUsage(input_other=10,input_cached=20,output=3),raw_completion={'id':'request-id','usage':{'prompt_tokens':30,'prompt_tokens_details':{'cached_tokens':20},'completion_tokens':3}})
        control=self.control(trace={'botId':'bot','cycleId':'cycle','workId':'work','phase':'planning'})
        with patch('astrbot.core.logger.info') as log:
            await BoundedProvider(Provider(reply),control).text_chat(prompt='private prompt',system_prompt='private system')
        payload=log.call_args.args[0]
        self.assertNotIn('private prompt',payload);self.assertNotIn('private system',payload)
        data=json.loads(payload.split(' ',1)[1]);self.assertEqual(data['cachedTokens'],20);self.assertEqual(data['phase'],'planning');self.assertEqual(data['responseId'],'request-id')
        self.assertEqual(seen,[{'prompt':'private prompt','system_prompt':'private system'}])
    async def test_preflight_rejection_is_not_charged_as_unknown_submission(self):
        async def reject(n,kw):
            error=RuntimeError('local guard rejected')
            error._fishbot_submission_stage='request_not_sent'
            raise error
        control=self.control()
        with self.assertRaises(RuntimeError):await BoundedProvider(Provider(reject),control).text_chat(prompt='fixture')
        self.assertEqual(control.used_tokens,0);self.assertEqual(control.used_models,0)
        self.assertTrue(control.failure.startswith('model_request_not_sent:'))
    async def test_provider_error_response_is_distinct_from_unknown_transport(self):
        async def reject(n,kw):
            error=RuntimeError('provider rejection')
            error._fishbot_submission_stage='provider_response_received'
            raise error
        control=self.control()
        with self.assertRaises(RuntimeError):await BoundedProvider(Provider(reject),control).text_chat(prompt='fixture')
        self.assertGreater(control.used_tokens,0);self.assertEqual(control.used_models,1)
        self.assertTrue(control.failure.startswith('model_request_failed:'))

    async def test_native_tools_feed_the_next_model_turn(self):
        observed=[]
        class Read(FunctionTool):
            async def call(self,context):
                self.asserted=context.context.principal;observed.append(self.asserted);return {'source':'actual-fixture','value':7}
        tool=Read(name='read',description='read fixture',parameters={'type':'object','properties':{}})
        async def reply(n,kw):
            if n==1:return LLMResponse(role='assistant',tools_call_name=['read'],tools_call_args=[{}],tools_call_ids=['call-1'],usage=TokenUsage(input_other=10,output=10))
            self.assertIn('actual-fixture',str(kw));return LLMResponse(role='assistant',completion_text='observed 7',usage=TokenUsage(input_other=15,output=10))
        result=await run(Provider(reply),self.control(),'read','system',tools=[tool],context=SimpleNamespace(principal='autonomous-service'))
        self.assertEqual(result['status'],'completed');self.assertEqual(result['text'],'observed 7');self.assertEqual(result['usage']['modelCalls'],2);self.assertEqual(result['usage']['toolCalls'],1);self.assertEqual(observed,['autonomous-service'])

    async def test_no_native_forced_final_request_over_budget(self):
        class Read(FunctionTool):
            async def call(self,context):return 'again'
        async def reply(n,kw):return LLMResponse(role='assistant',tools_call_name=['read'],tools_call_args=[{}],tools_call_ids=['call-'+str(n)],usage=TokenUsage(input_other=10,output=10))
        provider=Provider(reply);result=await run(provider,self.control(model_calls=2),'loop','system',tools=[Read(name='read',description='fixture',parameters={'type':'object','properties':{}})])
        self.assertEqual(result['status'],'budget_exhausted');self.assertEqual(provider.calls,2);self.assertEqual(result['usage']['toolCalls'],2)

    async def test_real_model_and_tool_progress_refreshes_inactivity_timeout(self):
        class Read(FunctionTool):
            async def call(self,context):
                await asyncio.sleep(.1);return 'actual fixture result'
        async def reply(n,kw):
            await asyncio.sleep(.1)
            if n<=3:return LLMResponse(role='assistant',tools_call_name=['read'],tools_call_args=[{}],tools_call_ids=['read-'+str(n)],usage=TokenUsage(input_other=10,output=10))
            return LLMResponse(role='assistant',completion_text='finished the longer task',usage=TokenUsage(input_other=10,output=10))
        result=await run(Provider(reply),self.control(seconds=.4),'longer task','system',tools=[Read(name='read',description='fixture',parameters={'type':'object','properties':{}})])
        self.assertEqual(result['status'],'completed');self.assertGreater(result['seconds'],.4);self.assertEqual(result['usage']['modelCalls'],4);self.assertEqual(result['usage']['toolCalls'],3)

    async def test_stalled_model_times_out_without_manufacturing_progress(self):
        released=asyncio.Event()
        async def reply(n,kw):
            try:await asyncio.Event().wait()
            finally:released.set()
        result=await asyncio.wait_for(run(Provider(reply),self.control(seconds=.1),'stalled task','system'),3)
        self.assertEqual(result['status'],'budget_exhausted');self.assertEqual(result['reason'],'idle_timeout');self.assertTrue(released.is_set());self.assertEqual(result['usage']['modelCalls'],1);self.assertTrue(result['usage']['estimated'])

    async def test_interrupted_reassessment_retains_actual_public_search_checkpoint(self):
        from unittest.mock import patch
        import research_tools
        control=self.control();started=asyncio.Event()
        data={'source':'https://api.bilibili.com/search','at':123,'sha256':'fixture-hash','videos':[{'bvid':'BV1234567890','url':'https://www.bilibili.com/video/BV1234567890','title':'actual returned title','author':'fixture author','description':'public metadata only','secret':'must not copy unknown fields'}]}
        async def search(query):return data
        async def reply(n,kw):
            if n==1:return LLMResponse(role='assistant',tools_call_name=['bilibili_search'],tools_call_args=[{'query':'actual query'}],tools_call_ids=['search'],usage=TokenUsage(input_other=10,output=10))
            started.set();await asyncio.Event().wait()
        evidence=SimpleNamespace(append=control.evidence.append,checkpoint=control.checkpoints.append)
        with patch('research_tools.search_bilibili',search):
            task=asyncio.create_task(run(Provider(reply),control,'search','system',tools=research_tools.tools(lambda:True,evidence)))
            await asyncio.wait_for(started.wait(),3);control.cancel.set();result=await asyncio.wait_for(task,3)
        self.assertEqual(result['status'],'canceled');self.assertEqual(result['checkpoints'][0]['videos'][0]['title'],'actual returned title');self.assertNotIn('secret',result['checkpoints'][0]['videos'][0]);self.assertEqual(result['checkpoints'][0]['query'],'actual query');self.assertIn('未观看',result['checkpoints'][0]['meaning'])

    async def test_service_cooldown_withdraws_only_failed_tool_and_keeps_other_work(self):
        calls=[]
        class Remote(FunctionTool):
            async def call(self,context):
                calls.append('remote');return {'available':False,'scope':'service','code':'service_cooldown','retryAfterSeconds':900}
        class Local(FunctionTool):
            async def call(self,context):calls.append('local');return {'value':'actual local state'}
        async def reply(n,kw):
            names=kw['func_tool'].names()
            if n==1:return LLMResponse(role='assistant',tools_call_name=['remote','remote'],tools_call_args=[{},{}],tools_call_ids=['r1','r2'],usage=TokenUsage(input_other=10,output=10))
            self.assertNotIn('remote',names);self.assertIn('local',names)
            if n==2:return LLMResponse(role='assistant',tools_call_name=['local'],tools_call_args=[{}],tools_call_ids=['l1'],usage=TokenUsage(input_other=10,output=10))
            return LLMResponse(role='assistant',completion_text='local read completed; remote blocked',usage=TokenUsage(input_other=10,output=10))
        tools=[Remote(name='remote',description='remote',parameters={'type':'object','properties':{}}),Local(name='local',description='local',parameters={'type':'object','properties':{}})]
        result=await run(Provider(reply),self.control(tool_calls=4),'read','system',tools=tools)
        self.assertEqual(result['status'],'completed');self.assertEqual(calls,['remote','local']);self.assertEqual(result['usage']['toolCalls'],2)
        self.assertEqual([tool.name for tool in tools],['remote','local'],'one work disabled a tool for later work')
        self.assertEqual(result['toolResults'][0]['retryAfterSeconds'],900)

    async def test_tool_exception_is_returned_with_call_id_before_model_changes_tool(self):
        calls=[]
        class Failing(FunctionTool):
            async def call(self,context):
                calls.append('failing');raise ValueError('fixture dependency unavailable')
        class Alternative(FunctionTool):
            async def call(self,context):
                calls.append('alternative');return {'value':'actual alternative result'}
        async def reply(n,kw):
            if n==1:return LLMResponse(role='assistant',tools_call_name=['failing'],tools_call_args=[{}],tools_call_ids=['failed-call-id'],usage=TokenUsage(input_other=10,output=10))
            if n==2:
                self.assertIn('fixture dependency unavailable',str(kw));self.assertIn('failed-call-id',str(kw))
                return LLMResponse(role='assistant',tools_call_name=['alternative'],tools_call_args=[{}],tools_call_ids=['alternative-call-id'],usage=TokenUsage(input_other=10,output=10))
            self.assertIn('actual alternative result',str(kw));self.assertIn('alternative-call-id',str(kw))
            return LLMResponse(role='assistant',completion_text='task continued using alternative result',usage=TokenUsage(input_other=10,output=10))
        tools=[Failing(name='failing',description='fails locally',parameters={'type':'object','properties':{}}),Alternative(name='alternative',description='independent alternative',parameters={'type':'object','properties':{}})]
        result=await run(Provider(reply),self.control(),'task','system',tools=tools)
        self.assertEqual(result['status'],'completed');self.assertEqual(calls,['failing','alternative'])
        self.assertEqual(result['usage']['modelCalls'],3);self.assertEqual(result['usage']['toolCalls'],2)
        self.assertEqual([x['status'] for x in result['toolResults']],['failed','returned'])

    async def test_tool_budget_stops_without_forced_final_or_another_tool(self):
        calls=[]
        class Read(FunctionTool):
            async def call(self,context):calls.append('actual read');return 'read result'
        async def reply(n,kw):return LLMResponse(role='assistant',tools_call_name=['read'],tools_call_args=[{}],tools_call_ids=['call-'+str(n)],usage=TokenUsage(input_other=10,output=10))
        provider=Provider(reply);result=await run(provider,self.control(tool_calls=1),'task','system',tools=[Read(name='read',description='read',parameters={'type':'object','properties':{}})])
        self.assertEqual(result['status'],'budget_exhausted');self.assertEqual(result['reason'],'tool_call_budget')
        self.assertEqual(calls,['actual read']);self.assertEqual(provider.calls,2)
        self.assertEqual(result['usage']['toolCalls'],1)

    async def test_cancel_barrier_drains_tool_without_followup_model(self):
        started=asyncio.Event();released=asyncio.Event();control=self.control()
        class Read(FunctionTool):
            async def call(self,context):
                started.set()
                try:await asyncio.Event().wait()
                finally:released.set()
        async def reply(n,kw):return LLMResponse(role='assistant',tools_call_name=['read'],tools_call_args=[{}],tools_call_ids=['call-1'],usage=TokenUsage(input_other=10,output=10))
        provider=Provider(reply);task=asyncio.create_task(run(provider,control,'stop','system',tools=[Read(name='read',description='fixture',parameters={'type':'object','properties':{}})]))
        await asyncio.wait_for(started.wait(),3);control.cancel.set();result=await asyncio.wait_for(task,3)
        self.assertEqual(result['status'],'canceled');self.assertTrue(released.is_set());self.assertEqual(provider.calls,1)

    async def test_epoch_invalid_before_request_and_prompt_budget(self):
        async def reply(n,kw):self.fail('provider must not be called')
        result=await run(Provider(reply),self.control(current=lambda:False),'prompt','system')
        self.assertEqual(result['status'],'canceled');self.assertEqual(result['usage']['modelCalls'],0)
        result=await run(Provider(reply),self.control(tokens=1800),'x'*5000,'system')
        self.assertEqual(result['status'],'budget_exhausted');self.assertEqual(result['usage']['modelCalls'],0)

    async def test_missing_usage_is_charged_conservatively(self):
        async def reply(n,kw):return LLMResponse(role='assistant',completion_text='done')
        result=await run(Provider(reply),self.control(),'prompt','system')
        self.assertEqual(result['status'],'completed');self.assertTrue(result['usage']['estimated']);self.assertGreater(result['usage']['tokens'],1536)

    async def test_remaining_budget_bounds_the_real_request_output_and_restores_context(self):
        from autonomy import decision_token_limit
        original=decision_token_limit.get();control=self.control(tokens=1900)
        async def reply(n,kw):
            cap=decision_token_limit.get();self.assertGreaterEqual(cap,512);self.assertLess(cap,1536)
            self.assertLessEqual((len(json.dumps(kw,ensure_ascii=False,default=str).encode())+1)//2+cap,1900)
            return LLMResponse(role='assistant',completion_text='bounded reassessment',usage=TokenUsage(input_other=100,output=20))
        response=await BoundedProvider(Provider(reply),control).text_chat(contexts=[{'role':'user','content':'x'*2000}])
        self.assertEqual(response.completion_text,'bounded reassessment');self.assertEqual(control.used_tokens,120)
        self.assertEqual(len(control.output_caps),1);self.assertEqual(control.tokens,1900);self.assertEqual(decision_token_limit.get(),original)
        async def failed(n,kw):raise RuntimeError('controlled provider failure')
        with self.assertRaises(RuntimeError):await BoundedProvider(Provider(failed),self.control(tokens=1900)).text_chat(contexts=[{'role':'user','content':'x'*2000}])
        self.assertEqual(decision_token_limit.get(),original)


if __name__=='__main__':unittest.main()
