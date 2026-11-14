"""Configured declarations stay fixed; execution never inherits visibility rights."""
import asyncio,copy,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.agent.tool import FunctionTool,ToolSet
from behavior import BehaviorStore
from bot_pipeline import Work
from autonomous_runtime import AutonomousRuntime
import bilibili_tools,inner_tools,qq_actions,group_actions


class StableCatalog(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=BehaviorStore(self.root/'behavior.sqlite');self.addCleanup(self.store.close)
        self.settings={'root':str(self.root),'botId':'bot','tenantId':'tenant','generation':1,'revision':1,
                       'config':{'groups':['55555'],'behavior':{'ownerUin':'12345678','qqProfile':True,'qzone':True}}}
        self.operations=[{'operation':op,'write':op in ('like','coin','comment'),'scope':'public.read' if op not in ('like','coin','comment','analyze') else 'like.write' if op in ('like','coin','comment') else 'media.analyze','args':{'type':'object','properties':{}}} for op in list(bilibili_tools.DESCRIPTIONS)[:35]]
        self.assertEqual(len(self.operations),35)
        (self.root/'ai').mkdir()
        self.bridge={'version':1,'botId':'bot','generation':1,'revision':1,'settingsRevision':1,'allowAutonomous':True,'url':'http://127.0.0.1:1','token':'fixture','operations':self.operations}
        self.write_bridge()
        self.brain=AutonomousRuntime(NS(),{'bot':self.settings},lambda *args:self.store,None,{},None,None)
        self.brain.policies['bot']={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'epoch':1,'until':time.time()*1000+60000,'lease':self.settings,'config':{'mode':'active'}}

    def write_bridge(self):
        (self.root/'ai'/'bilibili-bridge.json').write_text(json.dumps(self.bridge),encoding='utf-8')

    def event(self,subject='12345678',group='',completion=False,text='你好'):
        scope='group:'+group if group else 'private:'+subject
        work=Work('bot',scope,subject,'99999999','event','tool_result' if completion else 'message',time.time(),self.settings)
        extras={'_hosted_pipeline':work,'_hosted_lease':self.settings}
        if completion:extras['_hosted_tool_completion']={'id':'result','senderId':subject,'scope':scope,'workId':'original','owner':'permit'}
        return NS(get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.update({k:v}),get_sender_id=lambda:subject,
                  get_self_id=lambda:'99999999',get_group_id=lambda:group,get_message_str=lambda:text,get_messages=lambda:[],message_obj=NS(message_id='event',raw_message={})),scope

    def tools(self,event,scope):
        result=inner_tools.features(self.settings,event,self.store.db,scope,lambda:True,evidence=[])
        result+=self.brain.foreground_tools('bot',event,lambda:True)
        return sorted([inner_tools.completion_guard(tool,event) for tool in result],key=lambda tool:tool.name)

    async def test_full_schema_bytes_order_and_35_bridge_operations_across_speakers_and_completions(self):
        expected=None
        for subject in ('12345678','22345678'):
            for group in ('','55555'):
                for completion in (False,True):
                    for text in ('你好','设置签名：今天摸鱼','不要发布QQ空间'):
                        event,scope=self.event(subject,group,completion,text)
                        tools=self.tools(event,scope)
                        wire=json.dumps(ToolSet(tools=tools).openai_schema(),ensure_ascii=False,separators=(',',':'))
                        self.assertEqual(sum(t.name.startswith('bilibili_') for t in tools),35)
                        if expected is None:expected=wire
                        self.assertEqual(wire,expected,(subject,group,completion,text))
        self.assertNotIn('permit',expected)
        print('PASS 35 bridge operations and all configured feature schemas byte-identical across 24 event variants')

    async def test_scene_switch_uses_on_demand_catalogs_without_automatic_replay(self):
        import quote_reply,qq_favorite_tools
        quote_reply.initialize(self.store.db)
        now=time.time()
        for group,subject,handle in (('55555','12345678','qr_group'),('','22345678','qr_private')):
            event,scope=self.event(subject,group)
            self.store.db.execute('INSERT INTO pipeline_quote_sources(handle,scope,sender,message,at,self_id,generation,preview,media) VALUES(?,?,?,?,?,?,?,?,?)',
                (handle,scope,subject,handle,now,'99999999',1,'visible '+handle,'[]'))
        for subject,group,expected in (('12345678','55555','qr_group'),('22345678','','qr_private'),('12345678','55555','qr_group')):
            event,scope=self.event(subject,group)
            event.set_extra('_hosted_context',json.dumps({'scope':scope,'sender_id':subject,'at':now}))
            with patch('quote_reply.catalog',wraps=quote_reply.catalog) as catalog,patch('qq_favorite_tools.context',wraps=qq_favorite_tools.context) as sources:
                tools={t.name:t for t in self.tools(event,scope)}
                # No quote snapshot is automatically constructed. Favorite tools
                # retain their private source validation; none enters the wire.
                self.assertEqual(catalog.call_count,0)
                wire=json.loads(inner_tools.request_context(event))
                self.assertNotIn('quote_reply',wire.get('tool_context',{}))
                self.assertNotIn('qq_favorites',wire.get('tool_context',{}))
                self.assertEqual(wire['scope'],scope)
                self.assertEqual(wire['sender_id'],subject)
            context=NS(context=NS(event=event))
            reply=json.loads(await tools['hosted_quote_reply'].call(context))
            self.assertEqual([r['handle'] for r in reply['data']['messages']],[expected])
            result=json.loads(await tools['hosted_qq_favorites'].call(context,operation='sources'))
            self.assertEqual(result['receipt']['status'],'not_applicable')
            self.assertEqual(result['data'],qq_favorite_tools.context(event,self.store.db))
            self.assertIsNone(event.get_extra('_hosted_quote_handle'))
            self.assertIn('sources',tools['hosted_qq_favorites'].parameters['properties']['operation']['enum'])
        # An obsolete Work cannot even read fresh catalogs through this tool.
        stale=qq_favorite_tools.tools(event,self.settings['config'],self.store.db,str(self.root),lambda:False)[0]
        result=json.loads(await stale.call(context,operation='sources'))
        self.assertEqual(result['receipt']['status'],'rejected')

    async def test_companion_query_tracks_current_scope_and_changed_state(self):
        import companion_state
        now=time.time()
        for scope,subject,topic in (('group:55555','12345678','group_pending'),('private:22345678','22345678','private_pending')):
            self.store.db.execute('INSERT INTO companion_motives VALUES(?,?,?,?,?,?,?)',(scope,subject,topic,now-1,now+300,'open',companion_state.motive_record('original-event','human_request')))
        for subject,group,topic in (('12345678','55555','group_pending'),('22345678','','private_pending'),('12345678','55555','group_pending')):
            event,scope=self.event(subject,group)
            tools={t.name:t for t in self.tools(event,scope)}
            self.assertEqual(sum(t.name=='inspect_context' for t in tools.values()),1)
            result=json.loads(await tools['inspect_context'].call(NS(context=NS(event=event))))
            self.assertEqual(result['receipt']['scope'],scope)
            self.assertEqual(result['receipt']['status'],'not_applicable')
            self.assertEqual([r['topic'] for r in result['data']['unfinished_topics']],[topic])
            self.assertEqual(result['data']['unfinished_topics'][0]['author'],'human_request')
        self.store.db.execute("UPDATE companion_motives SET status='closed' WHERE scope='group:55555'")
        result=json.loads(await tools['inspect_context'].call(NS(context=NS(event=event))))
        self.assertEqual(result['data']['unfinished_topics'],[])
        result=json.loads(await tools['inspect_context'].call(NS(context=NS(event=object()))))
        self.assertEqual(result['receipt']['status'],'rejected')
        stale=companion_state.inspect_tool(self.store.db,self.settings['config'],scope,lambda:False,event=event)
        result=json.loads(await stale.call(NS(context=NS(event=event))))
        self.assertEqual(result['receipt']['status'],'rejected')

    async def test_visible_tools_do_not_authorize_member_or_synthetic_effects(self):
        event,scope=self.event('22345678')
        tools={t.name:t for t in self.tools(event,scope)};context=NS(context=NS(event=event))
        with patch('bilibili_tools.aiohttp.ClientSession',side_effect=AssertionError('unauthorized network')):
            reply=json.loads(await tools['bilibili_like'].call(context))
            self.assertEqual(reply['receipt']['status'],'rejected')
        reply=json.loads(await tools['hosted_qq_publish'].call(context,content='bad'))
        self.assertEqual(reply['receipt']['status'],'rejected')
        reply=json.loads(await tools['hosted_qq_group_send'].call(context,group_id='55555',text='bad'))
        self.assertEqual(reply['receipt']['status'],'rejected')
        reply=json.loads(await tools['hosted_update_agreement'].call(context,action='propose',topic='bad'))
        self.assertEqual(reply['receipt']['status'],'rejected')
        event,scope=self.event(completion=True)
        tools=self.tools(event,scope);context=NS(context=NS(event=event))
        for tool in tools:
            if tool.name.startswith('bilibili_') and tool.name!='bilibili_analyze' or tool.name=='hosted_recall_experience':continue
            reply=json.loads(await tool.call(context))
            self.assertEqual(reply['receipt']['status'],'rejected',tool.name)
        self.assertEqual(qq_actions.allowed(event,self.settings['config']),set())
        self.assertFalse(group_actions.owner_request(event,self.settings['config']))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM qq_action_attempts').fetchone()[0],0)

    async def test_analysis_declaration_remains_but_execution_cannot_resubmit(self):
        event,scope=self.event(completion=True)
        tool=next(t for t in bilibili_tools.tools(self.settings,event,lambda:True,[]) if t.name=='bilibili_analyze')
        reply=json.loads(await tool.call(NS(context=NS(event=event))))
        self.assertEqual(reply['error'],'completion_cannot_resubmit_analysis')
        self.bridge['settingsRevision']=2;self.write_bridge()
        reply=json.loads(await tool.call(NS(context=NS(event=event))))
        self.assertEqual(reply['error'],'configuration_changed')

    async def test_z_actual_runner_reset_retains_native_and_feature_schemas_on_completion(self):
        from behavior import install_runtime
        from astrbot.core.provider.entities import ProviderRequest
        from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
        from astrbot.core.tools.message_tools import SendMessageToUserTool
        core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={}))
        runtime=install_runtime(core,{'bot':self.settings})
        core._hosted_autonomous.policies=self.brain.policies
        class Native(FunctionTool):
            async def call(self,context,**kwargs):raise AssertionError('synthetic native send dispatched')
        native_definition=SendMessageToUserTool()
        expected=None
        try:
            for subject,group,completion in [('12345678','',False),('22345678','55555',False),('12345678','',True),('22345678','55555',True)]:
                event,scope=self.event(subject,group,completion)
                event.get_platform_id=lambda:'bot';event.get_sender_name=lambda:'fixture';event.unified_msg_origin=scope
                request=ProviderRequest(prompt='real request',system_prompt='FIXED_SYSTEM',contexts=[],func_tool=ToolSet(tools=[Native(name=native_definition.name,description=native_definition.description,parameters=copy.deepcopy(native_definition.parameters))]))
                runner=ToolLoopAgentRunner()
                await runner.reset(provider=NS(provider_config={'id':'fixture'}),request=request,run_context=NS(context=NS(event=event,context=NS(get_config=lambda **kwargs:{}))),tool_executor=NS(),agent_hooks=NS())
                wire=json.dumps(request.func_tool.openai_schema(),ensure_ascii=False,separators=(',',':'))
                self.assertEqual(request.system_prompt,'FIXED_SYSTEM')
                if expected is None:expected=wire
                self.assertEqual(wire,expected)
                self.assertEqual(sum(t.name.startswith('bilibili_') for t in request.func_tool.tools),35)
                if completion:
                    native=next(t for t in request.func_tool.tools if t.name=='send_message_to_user')
                    self.assertEqual(json.loads(await native.call(NS(context=NS(event=event))))['receipt']['status'],'rejected')
        finally:runtime['close']()


if __name__=='__main__':unittest.main()
