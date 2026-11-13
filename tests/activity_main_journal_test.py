"""Pinned SDK HTTP wire and durable activation accounting; no network/QQ."""
import asyncio,copy,json,os,sys,tempfile,time,unittest,uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
import httpx
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.core.platform.platform_metadata import PlatformMetadata
from behavior import install_runtime,BehaviorStore
from activation_rounds import record,valid,settled,split_material
from context_protocol import source,model_json,SOURCE
from readonly_journal import ReadonlyJournal


class Activity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # This fixture tests Journal/SDK transport, with no runtime HTTP bridge.
        # Shared cycle-budget enforcement is exercised by its dedicated tests.
        self.budget_binding=patch('model_request_budget.bind',return_value=None)
        self.budget_binding.start()
        self.requests=[];self.tool_step=False;self.request_gate=None
        async def handler(request):
            body=json.loads(request.content);self.requests.append(body)
            if self.request_gate:
                entered,release=self.request_gate;entered.set();await release.wait()
            msg={'role':'assistant','content':'[NO_REPLY]'};finish='stop'
            if self.tool_step:
                self.tool_step=False;finish='tool_calls'
                msg={'role':'assistant','content':None,'tool_calls':[{'id':'read-context','type':'function','function':{'name':'inspect_context','arguments':'{}'}}]}
            return httpx.Response(200,json={'id':'fixture','model':'fixture','usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110},'choices':[{'index':0,'message':msg,'finish_reason':finish}]})
        with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda p,c:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            self.provider=ProviderOpenAIOfficial({'id':'activity-fixture','type':'openai_chat_completion','model':'fixture','key':['fixture'],'api_base':'https://fixture.invalid/v1'}, {})
        self.settings={'qq':{'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'expectedUin':'90000000','root':str(Path(TEMP.name)/uuid.uuid4().hex),'config':{'systemPrompt':'Synthetic stable persona','proactive':True,'behavior':{'memory':True,'enabled':True,'memeEnabled':False,'groupParticipationGroups':[],'naturalConversation':False,'memorySummary':False}}}}
        self.core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={'platform-model':self.provider}),platform_manager=NS(get_insts=lambda:[NS(meta=lambda:PlatformMetadata('aiocqhttp','fixture','qq'),bot=NS())]))
        self.runtime=install_runtime(self.core,self.settings);self.runtime['manage']('qq',{'action':'list'})
        self.brain=self.core._hosted_autonomous;self.st=self.runtime['stores']['qq']
        import shared_context
        shared_context.initialize(self.st.db);self.st.shared_context=True;self.st.reflection.shared_context=True
    async def asyncTearDown(self):
        self.budget_binding.stop()
        self.brain.close()
        self.runtime['release']('qq')
        await self.provider.client.close()
    async def activate(self,identifier=None,continuation=False):
        identifier=identifier or str(uuid.uuid4());cycle=str(uuid.uuid4())
        policy={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'epoch':1,'until':time.time()*1000+14000,'cycleId':cycle,'actionId':identifier,'config':{'mode':'active','rollout':'live','activityEnabled':True,'maxSleepSeconds':86400}}
        self.brain.install('qq',policy)
        work={k:policy[k] for k in ('tenantId','botId','generation','revision','epoch','cycleId')}
        work.update(id=identifier,principal='autonomous-service',expiresAt=time.time()*1000+30000,budget={'modelCalls':4,'toolCalls':4,'tokens':100000,'seconds':15,'externalActions':1})
        action={'kind':'research','target':'','objective':'Choose autonomously','autonomousMoment':not continuation,'continuationOfMoment':continuation,'goalSnapshot':{'goals':[{'id':'goal-one','title':'EXACT_CURRENT_GOAL','source':{'scope':'self'}}],'actions':[],'effects':[]}}
        if continuation:action['continuation']={'id':'effect-complete','parentActionId':'original','result':{'status':'completed','evidence':['actual result']}}
        return await self.brain.execute('qq',{'work':work,'action':action})
    async def test_same_main_wire_silent_count_tools_restart_and_busy(self):
        self.tool_step=True
        first=await self.activate();self.assertEqual(first['status'],'completed',first)
        self.assertEqual(len(self.requests),2)
        first_wire=self.requests[0]['messages'];second_wire=self.requests[1]['messages']
        self.assertEqual(second_wire[:len(first_wire)],first_wire)
        origin=source(first_wire[-1]);self.assertEqual(origin['kind'],'platform_service_not_user')
        self.assertIn('EXACT_CURRENT_GOAL',json.dumps(first_wire[-1],ensure_ascii=False))
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],1)
        self.assertIn('bot_shared',self.st.model_journals)
        self.assertFalse(any(isinstance(k,tuple) for k in self.st.model_journals))
        saved=copy.deepcopy(self.st.model_journals['bot_shared'].last.wire)
        self.st.model_journals.clear()  # Reload the actual durable Journal.
        second=await self.activate();self.assertEqual(second['status'],'completed',second)
        self.assertEqual(self.requests[-1]['messages'][:len(saved)],saved)
        print('ACTIVITY_WIRE_AUDIT '+json.dumps({'unit':'unicode_characters_of_canonical_json',
            'initial_messages':len(first_wire),'initial_chars':len(model_json(first_wire)),
            'initial_goal_service_chars':len(model_json(first_wire[-1])),
            'second_activation_new_messages':len(self.requests[-1]['messages'])-len(saved),
            'second_activation_new_chars':sum(len(model_json(m)) for m in self.requests[-1]['messages'][len(saved):]),
            'old_prefix_equal':True,'provider_tokens':'synthetic_fixture_not_real_usage'}))
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],2)
        await self.activate(continuation=True)
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],2)
        before=len(self.requests);self.st.active=1
        try:busy=await self.activate()
        finally:self.st.active=0
        self.assertEqual(busy['reason'],'foreground_work_busy');self.assertEqual(len(self.requests),before)
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],2)
    async def test_activation_and_journal_stage_rollback_atomically(self):
        await self.activate();journal=self.st.model_journals['bot_shared'];before=copy.deepcopy(journal.last.wire)
        with patch('activation_rounds.record',side_effect=RuntimeError('injected_transaction_failure')):
            result=await self.activate()
        self.assertNotEqual(result['status'],'completed',result)
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],1)
        reloaded=ReadonlyJournal(self.st.db,'bot_shared')
        self.assertEqual(reloaded.last.wire,before)

    async def test_scoped_goals_preserve_real_unknown_effects(self):
        from activity_context import visible_goals
        value={'goals':[{'id':'a','source':{'scope':'private:12345678'}},{'id':'b','source':{'scope':'group:87654321'}}],
               'actions':[{'id':'action-a','goal_id':'a'},{'id':'action-b','goal_id':'b'}],
               'effects':[{'id':'effect-a','work_id':'action-a','status':'unknown'},{'id':'effect-b','work_id':'action-b','status':'confirmed'}]}
        result=visible_goals(value,'private:12345678')
        self.assertEqual([g['id'] for g in result['goals']],['a'])
        self.assertEqual(result['effects'],[value['effects'][0]])
        self.assertEqual(len(value['effects']),2)

    async def test_concurrent_activation_skips_while_real_provider_in_flight(self):
        entered=asyncio.Event();release=asyncio.Event();self.request_gate=(entered,release)
        first=asyncio.create_task(self.activate())
        try:
            await asyncio.wait_for(entered.wait(),5)
            second=await self.brain.execute('qq',{'work':{},'action':{'autonomousMoment':True}})
            self.assertEqual(second['reason'],'foreground_work_busy')
            self.assertEqual(second['usage']['modelCalls'],0)
        finally:release.set()
        self.assertEqual((await first)['status'],'completed')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],1)

    async def test_nonshared_reuses_existing_full_private_conversation(self):
        from context_transport import EVENT_CONTEXT_CURRENT
        from readonly_journal import Prepared
        scope='private:12345678'
        self.st.shared_context=False;self.st.reflection.shared_context=False
        original=[{'role':'system','content':'Synthetic stable persona'},
                  {'role':'user','content':SOURCE+model_json({'scope':scope,'sender_id':'12345678','event_id':'human-before-activation'})+'\nRemember our exact ongoing conversation'},
                  {'role':'assistant','content':'The same main conversation remains here.'}]
        journal=ReadonlyJournal(self.st.db,scope);journal.serial=1
        prior_packet={'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'identity':{'scope':scope}},'policy':{},'currentGuidance':'Prior private conversation','meaning':'Trusted prior platform data'})}
        prior_state={'memory_foundation':{'conversation_summaries':[]}}
        self.assertTrue(journal.commit(Prepared(1,None,copy.deepcopy(original),copy.deepcopy(original)+[prior_packet],prior_state,1,time.time(),'initial',authority=prior_packet,retirement=self.st.reflection.reconstruction_stamp(scope))))
        old_wire=copy.deepcopy(journal.last.wire)
        result=await self.activate()
        self.assertEqual(result['status'],'completed',(result,self.st.model_journals[scope].refusal_evidence()))
        self.assertEqual(self.requests[0]['messages'][:len(old_wire)],old_wire)
        self.assertEqual(source(self.requests[0]['messages'][-1])['kind'],'platform_service_not_user')
        self.assertEqual(self.st.db.execute('SELECT scope FROM memory_rounds').fetchall(),[(scope,)])
        self.assertFalse(any(isinstance(k,tuple) for k in self.st.model_journals))
        # The SDK's private window never received the internal activation.
        # A real following input must still append to that exact main Journal.
        saved=copy.deepcopy(self.st.model_journals[scope].last.wire)
        service_policy=json.loads(self.st.model_journals[scope].last.authority['content'].split('\n',1)[1])['policy']
        self.st.model_journals.clear()
        from astrbot.core.platform.astrbot_message import MessageMember
        from astrbot.core.platform.astr_message_event import current_message_event
        event=self.brain.make_event('qq',{'id':'human-after-activation'},scope,'Continue our conversation','90000000')
        event.message_obj.sender=MessageMember(user_id='12345678',nickname='Synthetic person')
        event.message_obj.raw_message={'post_type':'message','message_type':'private','message_id':'human-after-activation','user_id':12345678,'self_id':90000000,'message':[{'type':'text','data':{'text':'Continue our conversation'}}]}
        event.set_extra('_hosted_internal_work',None);event.set_extra('_hosted_initiative',False)
        from context_foundation import read
        event.set_extra('_hosted_context',model_json({'identity':{'scope':scope,'sender_id':'12345678','self_id':'90000000','owner':False},'memory_foundation':read(self.st,self.settings['qq']['config']['behavior'],scope,'12345678'),'state':{}}))
        work=self.st.pipeline.bind(event,self.settings['qq'])
        token=current_message_event.set(event)
        try:
            await self.provider.text_chat(system_prompt='Synthetic stable persona',contexts=copy.deepcopy(original[1:]),
                prompt=SOURCE+model_json({'scope':scope,'sender_id':'12345678','event_id':work.event_id})+'\nContinue our conversation')
        finally:current_message_event.reset(token)
        self.assertEqual(self.requests[-1]['messages'][:len(saved)],saved)
        human_policy=json.loads(self.st.model_journals[scope].last.authority['content'].split('\n',1)[1])['policy']
        self.assertEqual(human_policy,service_policy)
        self.assertIn('仅当本轮身份为自主服务任务时',human_policy)
        from decision_history import apply_changes
        latest={}
        for message in self.requests[-1]['messages']:
            content=message.get('content')
            if isinstance(content,str) and content.startswith(EVENT_CONTEXT_CURRENT+'\n'):
                packet=json.loads(content.split('\n',1)[1])
                latest=apply_changes(latest,packet['eventChanges']) if 'eventChanges' in packet else packet
        self.assertEqual(latest['authority']['identity']['sender_id'],'12345678')
        suffix=model_json(self.requests[-1]['messages'][len(saved):])
        self.assertNotIn(human_policy,suffix)
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],1)

    async def test_other_modes_count_only_real_dialogue(self):
        identifier='service-dialogue';scope='private:12345678'
        self.st.db.execute('INSERT INTO pipeline_actions(id,scope,state,at) VALUES(?,?,?,?)',(identifier,scope,'completed',time.time()));self.st.db.commit()
        extras={'_hosted_initiative':True,'_hosted_autonomous_action':{'kind':'message'}}
        event=NS(get_platform_id=lambda:'qq',get_extra=lambda key,default=None:extras.get(key,default),get_message_str=lambda:'Service invitation, not a QQ author')
        work=NS(event_id=identifier,scope=scope,source='autonomous',subject='12345678',confirmed_messages=0,confirmed_reply_parts=[],confirmed_media_kinds=[],summary_materials=[])
        self.runtime['settle_work_round'](self.st,event,work,self.settings['qq'])
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
        work.confirmed_messages=1;work.confirmed_reply_parts=['Actually confirmed reply']
        self.runtime['settle_work_round'](self.st,event,work,self.settings['qq'])
        self.runtime['settle_work_round'](self.st,event,work,self.settings['qq'])
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],1)
        from shared_context import event_key
        from activation_rounds import metadata
        key=event_key({'scope':scope,'event_id':identifier})
        self.assertEqual(metadata(self.st.db,'bot_shared',key)['round_kind'],'service_dialogue')

    async def test_goal_tool_real_http_uses_bound_identity_without_bili_enabled(self):
        from aiohttp import web
        from autonomous_goals import tools
        requests=[]
        async def handler(request):
            requests.append(await request.json())
            self.assertEqual(request.headers['Authorization'],'Bearer synthetic-bridge')
            return web.json_response({'status':'updated','goal':{'id':'goal','revision':2}})
        app=web.Application();app.router.add_post('/internal/v1/bots/bot/autonomous-goals/update',handler)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        try:
            settings=self.settings['qq'];directory=Path(settings['root'])/'ai';directory.mkdir(exist_ok=True)
            (directory/'bilibili-bridge.json').write_text(model_json({'version':1,'botId':'bot','generation':1,'revision':1,'settingsRevision':1,'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]),'token':'synthetic-bridge','operations':[]}),encoding='utf-8')
            work=NS(scope='private:12345678',event_id='real-source')
            event=NS(get_extra=lambda key,default=None:work if key=='_hosted_pipeline' else default,get_sender_id=lambda:'12345678')
            tool=next(t for t in tools(settings,event,self.st.db,lambda:True) if t.name=='update_autonomous_goal')
            result=json.loads(await tool.call(NS(context=NS(event=event)),goalId='goal',expectedRevision=1,changes={'progress':'New actual understanding'}))
            self.assertEqual(result['goal']['revision'],2)
            self.assertEqual(requests[0]['trusted'],{'scope':'private:12345678','senderId':'12345678'})
            with self.assertRaises(asyncio.CancelledError):await tool.call(NS(context=NS(event=object())),goalId='goal',expectedRevision=1,changes={'status':'completed'})
            self.assertEqual(len(requests),1)
        finally:await runner.cleanup()


def load_tests(loader, standard_tests, pattern):
    # Discovery must retain the same one-install-per-Worker boundary as direct
    # execution. Each case still runs all original assertions in its subprocess.
    import subprocess
    def isolated(name):
        def run():
            result=subprocess.run([sys.executable,'-X','utf8','-B',__file__,'Activity.'+name],capture_output=True,text=True,encoding='utf-8',errors='replace')
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
        return unittest.FunctionTestCase(run,description=name)
    return unittest.TestSuite(isolated(name) for name in loader.getTestCaseNames(Activity)) if pattern is not None else standard_tests


if __name__=='__main__':
    if len(sys.argv)==1:
        # install_runtime patches SDK classes once per Worker process. Separate
        # real Workers, rather than stacking two incompatible runtime owners.
        import subprocess
        codes=[subprocess.run([sys.executable,'-X','utf8','-B',__file__,'Activity.'+name]).returncode
               for name in unittest.defaultTestLoader.getTestCaseNames(Activity)]
        sys.exit(max(codes,default=0))
    unittest.main()
