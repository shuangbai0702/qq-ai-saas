"""SDK pipeline through both real launcher gates; all QQ/model transports mocked."""
import ast,asyncio,os,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot')]

def gate(file,namespace):
    tree=ast.parse((ROOT/'adapters/astrbot'/file).read_text(encoding='utf8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='gated')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),file,'exec'),namespace)
    return namespace['gated']

class SDK(unittest.IsolatedAsyncioTestCase):
    async def test_both_launchers_route_private_group_pokes_and_ignore_nonwhite_and_reactions(self):
        sdk_root=tempfile.TemporaryDirectory()
        self.addCleanup(sdk_root.cleanup)
        os.environ['ASTRBOT_ROOT']=sdk_root.name
        import runtime_bootstrap
        runtime_bootstrap.initialize_runtime_bootstrap()
        from slim_bootstrap import install
        install()
        from astrbot.core.pipeline.scheduler import PipelineScheduler
        from astrbot.core.platform.astr_message_event import current_message_event
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember
        from astrbot.core.platform.platform_metadata import PlatformMetadata
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.message.components import Plain
        from behavior import install_runtime
        from native_ingress import admitted,bind
        from aiocqhttp import Event
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
        import worker_lifecycle
        pokes=[];model_inputs=[]
        async def pipeline(_,event):
            # This is the SDK model boundary after hosted observe/admission/context.
            model_inputs.append((event.get_message_str(),event.get_extra('_hosted_context')))
        PipelineScheduler.execute=pipeline
        cfg={'groups':[],'trigger':'all','behavior':{'groupParticipationGroups':['12345','12346'],
            'pokeReply':True,'pokeProbability':1,'memory':False,'typingSimulation':False,
            'burstDebounceMs':0,'cooldownSeconds':0,'quietStart':0,'quietEnd':0}}
        async def send_poke(**params):pokes.append(params)
        with tempfile.TemporaryDirectory() as tmp:
            lease={'tenantId':'t','botId':'b','root':tmp,'generation':1,'revision':1,'config':cfg}
            settings={'qq':lease}
            core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={}))
            runtime=install_runtime(core,settings)
            def create(message):
                ev=AiocqhttpMessageEvent(message.message_str,message,PlatformMetadata('aiocqhttp','fixture','qq'),message.session_id,bot=NS(send_poke=send_poke))
                ev.set_extra('_hosted_lease',lease);ev.set_extra('_hosted_generation',1)
                return ev
            async def original(message):await PipelineScheduler.execute(None,create(message))
            member={'uin':'99999','modelConfigured':False,'events':0}
            shared=gate('shared-worker.py',{'id':'qq','member':member,'members':{'qq':member},'core':core,
                'behavior_settings':settings,'platform':NS(create_event=create),'behavior':runtime,'errors':{},
                'worker_lifecycle':worker_lifecycle,'stop_fence':NS(quiescing=False),'original':original})
            dedicated=gate('headless.py',{'manifest':{'expectedUin':'99999','config':cfg},'admitted':admitted,'original':original})
            adapter=AiocqhttpAdapter.__new__(AiocqhttpAdapter)
            async def convert(raw):return await AiocqhttpAdapter.convert_message(adapter,raw)
            platform=NS(convert_message=convert)
            bind(platform,lambda raw:self.fail('notice acquired recovery identity'),lambda raw:admitted(raw,cfg))
            async def deliver(launcher,raw):
                msg=await platform.convert_message(raw)
                if msg is not None:await launcher(msg)
            counter=0
            try:
                for index,launcher in enumerate((shared,dedicated)):
                    for group in ('',str(12345+index)):
                        counter+=1;sender=str(11000+counter)
                        def message(ai=False,notice='notify',target_group=group):
                            cfg['behavior']['pokeIntoAiContext']=ai
                            return Event({'post_type':'notice','notice_type':notice,'sub_type':'poke','self_id':'99999','target_id':'99999','user_id':sender,**({'group_id':target_group} if target_group else {})})
                        before=(len(pokes),len(model_inputs))
                        await deliver(launcher,message())
                        self.assertEqual((len(pokes),len(model_inputs)),(before[0]+1,before[1]))
                        await deliver(launcher,message(ai=True))
                        self.assertEqual((len(pokes),len(model_inputs)),(before[0]+1,before[1]+1))
                        self.assertIn('协议通知，不是文字发言',model_inputs[-1][0])
                        self.assertNotIn('不可信原始通知正文',model_inputs[-1][0])
                        await deliver(launcher,message(ai=True,target_group='54321'))
                        await deliver(launcher,message(ai=True,notice='group_msg_emoji_like'))
                        self.assertEqual((len(pokes),len(model_inputs)),(before[0]+1,before[1]+1))
                st=runtime['stores']['qq']
                self.assertEqual(st.db.execute('SELECT count(*) FROM pipeline_work_inputs').fetchone()[0],0)
                self.assertEqual(st.db.execute('SELECT count(*) FROM pipeline_events').fetchone()[0],4)
                self.assertEqual(st.scope_turns,{})
            finally:runtime['close']()

if __name__=='__main__':unittest.main()
