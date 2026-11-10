import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from compression_admin import manage
import autonomy


class CompressionAdmin(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[];self.gate=asyncio.Event();self.tracked=[]
        async def run():
            await self.gate.wait()
            return {'ok':True,'checkpointId':7}
        def start(scope,**kw):
            self.calls.append((scope,kw));self.reflection.task=asyncio.create_task(run())
            return {'started':True,'reason':'summary'}
        self.reflection=NS(force_compress_once=start,task=None)
        self.store=NS(shared_context=True,reflection=self.reflection)
        self.care=NS(busy=lambda st:bool(st.reflection.task and not st.reflection.task.done()),track=lambda st,task:self.tracked.append(task))
        self.data={'action':'admin_force_compress','requestId':'11111111-1111-4111-8111-111111111111','administrator':'admin-user','allowPartial':False}

    async def asyncTearDown(self):
        self.gate.set()
        if self.reflection.task:await self.reflection.task
        await asyncio.sleep(0)

    async def test_one_task_duplicate_and_completion_status(self):
        self.assertEqual(manage(self.store,self.data,self.care)['state'],'running')
        self.assertEqual(manage(self.store,self.data,self.care)['state'],'running')
        other={**self.data,'requestId':'22222222-2222-4222-8222-222222222222'}
        self.assertEqual(manage(self.store,other,self.care)['status'],409)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.tracked),1)
        self.assertFalse(self.calls[0][1]['allow_partial'])
        self.gate.set();await self.reflection.task;await asyncio.sleep(0)
        status=manage(self.store,{'action':'admin_force_compress_status','requestId':self.data['requestId']},self.care)
        self.assertEqual(status['state'],'completed');self.assertEqual(status['result']['checkpointId'],7)

    async def test_strict_command_and_known_runtime_only(self):
        for data in ({**self.data,'is_admin':True},{**self.data,'allowPartial':'true'},{**self.data,'administrator':''}):
            self.assertEqual(manage(self.store,data,self.care)['status'],400)
        self.store.shared_context=False
        self.assertEqual(manage(self.store,self.data,self.care)['status'],409)
        self.assertEqual(self.calls,[])

    async def test_cancelled_operation_is_reported(self):
        manage(self.store,self.data,self.care)
        self.reflection.task.cancel()
        with self.assertRaises(asyncio.CancelledError):await self.reflection.task
        await asyncio.sleep(0)
        self.assertEqual(manage(self.store,self.data,self.care)['state'],'cancelled')
        self.reflection.task=None

    async def test_failure_is_observed_without_exposing_error_content(self):
        async def fail():raise RuntimeError('private model details')
        def start(scope,**kwargs):
            self.reflection.task=asyncio.create_task(fail())
            return {'started':True,'reason':'summary'}
        self.reflection.force_compress_once=start
        manage(self.store,self.data,self.care)
        with self.assertRaises(RuntimeError):await self.reflection.task
        await asyncio.sleep(0)
        status=manage(self.store,self.data,self.care)
        self.assertEqual(status['state'],'failed');self.assertEqual(status['reason'],'RuntimeError')
        self.assertNotIn('private',str(status));self.reflection.task=None

    async def test_unconfirmed_completion_is_not_success(self):
        async def empty():return None
        def start(scope,**kwargs):
            self.reflection.task=asyncio.create_task(empty())
            return {'started':True,'reason':'summary'}
        self.reflection.force_compress_once=start
        manage(self.store,self.data,self.care);await self.reflection.task;await asyncio.sleep(0)
        status=manage(self.store,self.data,self.care)
        self.assertEqual(status['state'],'failed');self.assertEqual(status['reason'],'completion_unconfirmed')

    async def test_admin_task_inherits_budget_and_caller_restores_previous_context(self):
        observed=[]
        async def run():
            await self.gate.wait();observed.append(autonomy.decision_token_limit.get())
            return {'ok':True}
        def start(scope,**kwargs):
            self.reflection.task=asyncio.create_task(run())
            return {'started':True}
        self.reflection.force_compress_once=start
        token=autonomy.decision_token_limit.set(2048)
        try:
            manage(self.store,self.data,self.care)
            self.assertEqual(autonomy.decision_token_limit.get(),2048)
            async def other():return autonomy.decision_token_limit.get()
            self.assertEqual(await asyncio.create_task(other()),2048)
            self.gate.set();await self.reflection.task
            self.assertEqual(observed,[16384]);self.assertEqual(autonomy.decision_token_limit.get(),2048)
        finally:autonomy.decision_token_limit.reset(token)
        self.assertIsNone(autonomy.decision_token_limit.get())

    async def test_budget_restored_when_start_raises(self):
        def start(scope,**kwargs):raise RuntimeError('start failed')
        self.reflection.force_compress_once=start
        with self.assertRaises(RuntimeError):manage(self.store,self.data,self.care)
        self.assertIsNone(autonomy.decision_token_limit.get())

    async def test_real_sdk_wire_uses_admin_budget_without_mutating_provider(self):
        import json,os,tempfile
        from unittest.mock import patch
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'references/astrbot'))
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'ASTRBOT_ROOT':root}):
            import runtime_bootstrap
            runtime_bootstrap.initialize_runtime_bootstrap()
            from slim_bootstrap import install
            install()
            import httpx
            from behavior import install_runtime
            from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
            wire=[]
            async def handler(request):
                wire.append(json.loads(request.content))
                return httpx.Response(200,json={'id':'offline','model':'offline','choices':[{'index':0,'message':{'role':'assistant','content':'summary'},'finish_reason':'stop'}]})
            original=ProviderOpenAIOfficial._create_http_client
            ProviderOpenAIOfficial._create_http_client=lambda self,c:httpx.AsyncClient(transport=httpx.MockTransport(handler))
            runtime=install_runtime(NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={})),{})
            provider=ProviderOpenAIOfficial({'id':'offline','type':'openai_chat_completion','model':'offline','key':['fixture'],'api_base':'https://fixture.invalid/v1','custom_extra_body':{'max_tokens':8192}}, {})
            async def run():
                await provider.text_chat(prompt='admin summary',contexts=[],func_tool=None)
                return {'ok':True}
            def start(scope,**kwargs):
                self.reflection.task=asyncio.create_task(run());return {'started':True}
            self.reflection.force_compress_once=start
            try:
                manage(self.store,self.data,self.care);await self.reflection.task
                self.assertEqual(wire[-1]['max_tokens'],16384)
                self.assertEqual(provider.provider_config['custom_extra_body'],{'max_tokens':8192})
                await provider.text_chat(prompt='ordinary automatic request',contexts=[],func_tool=None)
                self.assertEqual(wire[-1]['max_tokens'],8192)
                provider.provider_config['custom_extra_body']={'max_completion_tokens':8192}
                second={**self.data,'requestId':'22222222-2222-4222-8222-222222222222'}
                manage(self.store,second,self.care);await self.reflection.task
                self.assertEqual(wire[-1]['max_completion_tokens'],16384);self.assertNotIn('max_tokens',wire[-1])
                self.assertEqual(provider.provider_config['custom_extra_body'],{'max_completion_tokens':8192})
                self.assertIsNone(autonomy.decision_token_limit.get());self.assertEqual(len(wire),3)
            finally:
                await provider.client.close();runtime['close']();ProviderOpenAIOfficial._create_http_client=original

if __name__=='__main__':unittest.main()
