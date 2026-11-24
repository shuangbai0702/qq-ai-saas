"""Original autonomous Work/RunControl ownership and authenticated callbacks."""
import asyncio,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from unittest.mock import patch
from contextlib import nullcontext
sys.path[:0]=[str(Path('adapters/astrbot').resolve()),str(Path('references/astrbot').resolve())]
from aiohttp import web
from astrbot.core.agent.run_context import ContextWrapper
from bot_pipeline import BotPipeline
from native_internal import RunControl,WorkBudgetExceeded
from dsh_tools import ModelBudget,budget_scope,parent_record,resume_autonomous,tools
from group_actions import tool_action_id
import dsh_read_tools


class AutonomousBridge(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings={'root':self.temp.name,'botId':'bot','tenantId':'tenant','generation':1,'revision':2,'config':{'behavior':{}}}
        self.identity={**{k:self.settings[k] for k in ('botId','tenantId','generation','revision')},'id':'new-cycle','cycleId':'new-cycle',
                       'epoch':3,'principal':'autonomous-service','expiresAt':(time.time()+60)*1000}
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);BotPipeline(self.db)
        self.extra={'_hosted_internal_work':self.identity,'_hosted_main_activation':True};self.live=True
        self.event=NS(get_extra=lambda key,default=None:self.extra.get(key,default),set_extra=lambda key,value:self.extra.__setitem__(key,value),
                      get_self_id=lambda:'99999',get_sender_id=lambda:'0',get_group_id=lambda:'')
        self.work=BotPipeline(self.db).bind_internal(self.event,self.settings,self.identity,'self','original purpose')
        self.control=RunControl(3,5,5000,60,lambda:self.live);self.control.output_tokens=640;self.control.model_budget=AsyncMock()
        self.context=ContextWrapper(context=NS(event=self.event,hosted_run_control=self.control))
        self.payloads=[];self.acks={};self.stale_settle=False;self.pending=False
        async def ack(request):
            value=await request.json();self.acks[value['id']].set_result(value);return web.json_response({})
        async def delegate(request):
            self.payloads.append(await request.json());response=web.StreamResponse(headers={'Content-Type':'application/x-ndjson'});await response.prepare(request)
            for phase,fields in [('reserve',{'inputTokens':100,'outputTokens':8192}),('tool_budget',{}),('settled',{'tokens':50})]:
                key=phase;self.acks[key]=asyncio.get_running_loop().create_future()
                if self.stale_settle and phase=='settled':self.live=False
                await response.write((json.dumps({'type':'tool','id':key,'operation':'tool_budget' if phase=='tool_budget' else 'model_budget','requestId':'dsh:one','phase':phase,**fields})+'\n').encode())
                result=await asyncio.wait_for(self.acks[key],3)
                if not result['ok']:await response.write_eof();return response
            await response.write((json.dumps({'type':'pending'} if self.pending else {'type':'final','text':'final text only'})+'\n').encode());await response.write_eof();return response
        app=web.Application();app.router.add_post('/delegate',delegate);app.router.add_post('/tool-result',ack)
        self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.bridge={'version':2,'mode':'local',**{k:self.settings[k] for k in ('botId','tenantId','generation','revision')},
                     'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/delegate','token':'a'*64}
        file=Path(self.temp.name)/'ai/dsh-bridge.json';file.parent.mkdir();file.write_text(json.dumps(self.bridge),encoding='utf8')

    async def asyncTearDown(self):await self.runner.cleanup()

    async def test_real_ndjson_budget_and_service_parent(self):
        token=tool_action_id.set('tool-call')
        try:result=await tools(self.settings,self.event,lambda:self.live,self.db,self.bridge)[0].call(self.context,goal='research')
        finally:tool_action_id.reset(token)
        self.assertEqual(result,'final text only');parent=self.payloads[0]['parent']
        self.assertEqual((parent['authority'],parent['work_id'],parent['mode_epoch'],parent['sender_id']),('autonomous','new-cycle',3,'service:bot'))
        self.assertEqual((self.control.used_models,self.control.used_tokens),(1,50))
        self.assertEqual(self.control.used_tools,1)
        self.assertEqual(self.acks['reserve'].result()['value'],{'maxTokens':640})
        self.assertEqual(self.control.model_budget.await_args_list[0].args,('dsh:one','reserve',740))
        self.assertEqual(self.control.model_budget.await_args_list[1].args,('dsh:one','settled',50))

    async def test_nested_tool_quota_is_the_same_run_control_ceiling(self):
        self.control.tool_calls=0;token=tool_action_id.set('tool-call')
        try:result=await tools(self.settings,self.event,lambda:self.live,self.db,self.bridge)[0].call(self.context,goal='research')
        finally:tool_action_id.reset(token)
        self.assertIn('委托执行未完成',result)
        self.assertFalse(self.acks['tool_budget'].result()['ok']);self.assertEqual(self.control.used_tools,0)
        self.assertTrue(self.control.estimated_usage)

    async def test_stale_result_still_settles_original_budget(self):
        self.stale_settle=True;token=tool_action_id.set('tool-call')
        try:
            with self.assertRaises(asyncio.CancelledError):await tools(self.settings,self.event,lambda:self.live,self.db,self.bridge)[0].call(self.context,goal='research')
        finally:tool_action_id.reset(token)
        self.assertEqual(self.control.used_tokens,50);self.assertTrue(self.acks['settled'].result()['ok'])

    async def test_resume_uses_new_cycle_and_old_delegation_pending_stays_internal(self):
        self.pending=True
        result=await resume_autonomous(self.settings,self.event,lambda:self.live,self.db,self.context,{'id':'media-original','owner':'claimed-owner','dsh':{'delegationId':'old-delegation'}})
        self.assertIsNone(result);payload=self.payloads[0]
        self.assertEqual(payload['parent']['work_id'],'new-cycle');self.assertEqual(payload['delegationId'],'old-delegation')
        self.assertEqual(payload['completion'],{'id':'media-original','owner':'claimed-owner'});self.assertNotIn('input',payload)

    async def test_budget_ceiling_unknown_and_release(self):
        self.control.model_calls=1;b=ModelBudget(self.control)
        await b.transition({'phase':'reserve','requestId':'one','inputTokens':100})
        with self.assertRaises(WorkBudgetExceeded):await b.transition({'phase':'reserve','requestId':'two','inputTokens':1})
        with self.assertRaises(ValueError):await b.transition({'phase':'reserve','requestId':'one','inputTokens':1})
        await b.transition({'phase':'released','requestId':'one'})
        self.assertEqual((self.control.used_models,self.control.used_tokens),(0,0))
        async with budget_scope(self.control) as budget:await budget.transition({'phase':'reserve','requestId':'lost','inputTokens':100})
        self.assertEqual((self.control.used_models,self.control.used_tokens),(1,740));self.assertTrue(self.control.estimated_usage)
        self.assertEqual(self.control.model_budget.await_args.args,('lost','unknown',740))

    async def test_scope_reader_reuses_real_internal_identity_and_rejects_foreign(self):
        rows=dsh_read_tools.catalog(self.settings,self.event,lambda:self.live,self.db,self.context)
        self.assertIn('hosted_recall_experience',{row['id'] for row in rows})
        self.identity['epoch']=99
        with self.assertRaisesRegex(ValueError,'parent_stale'):parent_record(self.settings,self.event,lambda:self.live,self.db,self.context)

    async def test_real_invoke_resumes_before_main_and_pending_never_submits_main(self):
        import autonomous_runtime
        completion={'id':'media-original','owner':'claimed-owner','dsh':{'delegationId':'old-delegation'},'result':{'private':'RAW INTERNAL RECEIPT'}}
        self.extra['_hosted_autonomous_action']={'continuation':completion}
        store=NS(wait_compression=AsyncMock(),active=0,waiting=0,reflection=NS(epoch=lambda:1),
                 pipeline=NS(handoff_current=lambda goal:True),db=self.db,slot_changed=asyncio.Event(),model_journals={})
        runtime=autonomous_runtime.AutonomousRuntime(NS(),{'bot':self.settings},lambda *args:store,lambda bot:NS(),{},NS(stamp=lambda root:None),None)
        runtime.valid=lambda *args,**kwargs:self.live
        runtime.control=lambda *args:self.control
        final={'status':'completed','text':'main final','seconds':0,'usage':{'tokens':1,'modelCalls':1,'toolCalls':0}}
        with patch('model_request_budget.bind',return_value=self.control.model_budget),patch('model_cache.decision_scope',return_value=nullcontext()),patch('autonomous_runtime.run',new_callable=AsyncMock,return_value=final) as main:
            result=await runtime.invoke('bot',self.identity,json.dumps({'continuation':completion}),'system',context=self.context.context)
            self.assertEqual(result,final);self.assertEqual(main.await_count,1)
            actual_prompt=main.await_args.args[2]
            self.assertIn('final text only',actual_prompt)
            self.assertNotIn('RAW INTERNAL RECEIPT',actual_prompt);self.assertNotIn('claimed-owner',actual_prompt);self.assertNotIn('old-delegation',actual_prompt)
            self.pending=True;main.reset_mock()
            result=await runtime.invoke('bot',self.identity,json.dumps({'continuation':completion}),'system',context=self.context.context)
            self.assertEqual(result['reason'],'dsh_media_pending');main.assert_not_awaited()
            self.assertTrue(self.extra['_hosted_dsh_completion_accepted']);self.assertEqual(store.active,0)
            self.control.cancel.clear();entered=asyncio.Event();stopped=asyncio.Event()
            async def blocked(*args):
                entered.set()
                try:await asyncio.Event().wait()
                finally:stopped.set()
            with patch('dsh_tools.resume_autonomous',side_effect=blocked):
                task=asyncio.create_task(runtime.invoke('bot',self.identity,'platform activation','system',context=self.context.context))
                await entered.wait();self.control.cancel.set()
                from native_internal import WorkStopped
                with self.assertRaises(WorkStopped):await task
                self.assertTrue(stopped.is_set());self.assertEqual(store.active,0)


if __name__=='__main__':unittest.main()
