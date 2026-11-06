"""Actual hosted provider injection; captures its request without a network call."""
import asyncio,json,os,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.core.platform.astr_message_event import current_message_event
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.message_type import MessageType
from astrbot.core.message.components import Plain,At
from astrbot.core.pipeline.scheduler import PipelineScheduler
from behavior import install_runtime

async def main():
    requests=[];pipeline_requests=[]
    async def original_pipeline(scheduler,event):
        pipeline_requests.append(event.get_extra('provider_request'))
    PipelineScheduler.execute=original_pipeline
    async def get_cid(umo):return 'existing-session'
    async def get_conversation(umo,cid):return NS(cid=cid,history='[]')
    async def capture(provider,prompt,system_prompt='',contexts=None,func_tool=None,**kwargs):
        requests.append({'prompt':prompt,'system':system_prompt,'contexts':contexts});return NS(completion_text='captured',raw_completion=None)
    ProviderOpenAIOfficial.text_chat=capture
    settings={};core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={}),conversation_manager=NS(get_curr_conversation_id=get_cid,get_conversation=get_conversation))
    runtime=install_runtime(core,settings);brain=core._hosted_autonomous
    try:
        for index,(meme,active) in enumerate([(True,True),(False,True),(True,False)]):
            id='prompt-'+str(index)
            settings[id]={'tenantId':'tenant','botId':id,'generation':1,'revision':1,'root':str(Path(TEMP.name)/id),'config':{'behavior':{'memeEnabled':meme,'memory':False}}}
            runtime['manage'](id,{'action':'list'})
            runtime['metrics'][id]={'requests':0,'successes':0,'failures':0}
            brain.install(id,{'tenantId':'tenant','botId':id,'generation':1,'revision':1,'epoch':1,'until':time.time()*1000+14000,'config':{'mode':'active' if active else 'passive','rollout':'live','capabilities':[]}})
            msg=AstrBotMessage();msg.type=MessageType.FRIEND_MESSAGE;msg.self_id='90000000';msg.session_id='12345678';msg.message_id='prompt-event-'+str(index);msg.sender=MessageMember('12345678','fixture');msg.message=[Plain('稍后再联系我')];msg.message_str='稍后再联系我';msg.raw_message={'post_type':'message'}
            event=AiocqhttpMessageEvent(msg.message_str,msg,PlatformMetadata('aiocqhttp','fixture',id),'12345678',bot=None)
            event.set_extra('_hosted_lease',settings[id]);event.set_extra('_hosted_generation',1)
            tools=brain.foreground_tools(id,event,lambda:True)
            assert any(t.name=='propose_background_task' for t in tools)==active
            assert [t.name for t in tools]==(['propose_background_task'] if active else []),'durable delegation must not itself grant execution permissions'
            token=current_message_event.set(event)
            try:await ProviderOpenAIOfficial.text_chat(NS(),prompt=event.get_message_str(),system_prompt='original persona',contexts=[])
            finally:current_message_event.reset(token)
            system=requests[-1]['system']
            from context_transport import EVENT_CONTEXT_CURRENT
            current_packet=next(json.loads(m['content'].split('\n',1)[1]) for m in requests[-1]['contexts'] if isinstance(m.get('content'),str) and m['content'].startswith(EVENT_CONTEXT_CURRENT+'\n'))
            policy=current_packet['policy']
            from bot_pipeline import EXECUTION_DISCLOSURE_POLICY
            assert (system+policy).count(EXECUTION_DISCLOSURE_POLICY)==1,'outward disclosure policy missing or duplicated'
            assert ('[后台任务登记]' in policy)==active,('handoff prompt missing',meme,active)
            if active:
                assert '本轮即时任务由内层直接使用当前已授权工具完成' in policy
                assert '不先自行执行后补登记' not in policy
                assert '以下规则适用于本轮决定参与交流之后' in policy
            assert ('目的会话的发送回执' in policy)==active,'foreground may claim a cross-conversation action without its receipt'
            assert ('[本轮图片表达规则，以此为准]' in policy)==meme
            assert 'original persona' in system
            event.set_extra('_hosted_context',json.dumps({'conversation_candidate':True}))
            token=current_message_event.set(event)
            try:await ProviderOpenAIOfficial.text_chat(NS(),prompt='candidate',system_prompt='original persona',contexts=[])
            finally:current_message_event.reset(token)
            candidate_system=requests[-1]['system'];marker='[群聊参与判断，先于回复格式与任务执行]'
            current_context=requests[-1]['contexts'][-1]
            from context_protocol import READONLY_CURRENT
            assert current_context['role']=='user' and current_context['content'].startswith(READONLY_CURRENT)
            assert candidate_system==system,'An event participation change must not rewrite System'
            candidate_packet=next(json.loads(m['content'].split('\n',1)[1]) for m in requests[-1]['contexts'] if isinstance(m.get('content'),str) and m['content'].startswith(EVENT_CONTEXT_CURRENT+'\n'))
            assert marker in candidate_packet['currentGuidance'] and '本轮不产生发送或其他外部写入' in candidate_packet['currentGuidance']
            assert marker not in current_context['content'],'event rules must not be mixed into historical memory'
            assert '后端' in candidate_system and '重新授权' in candidate_system
            if active:assert '[后台任务登记]' in candidate_packet['policy']
        # Exercise the actual patched native reset, not just the pure limiter.
        from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
        from astrbot.core.agent.run_context import ContextWrapper
        from astrbot.core.agent.hooks import BaseAgentRunHooks
        from astrbot.core.provider.entities import ProviderRequest
        from native_history import LogicalTurnLimiter
        owned=ToolLoopAgentRunner()
        await owned.reset(provider=NS(provider_config={'id':'fixture','max_context_tokens':100000}),request=ProviderRequest(prompt='fixture',contexts=[]),run_context=ContextWrapper(context=NS(event=event)),tool_executor=NS(),agent_hooks=BaseAgentRunHooks(),enforce_max_turns=30)
        assert isinstance(owned.request_context_manager.truncator,LogicalTurnLimiter)
        assert owned.request_context_manager.config.enforce_max_turns==30 and owned.request_context_manager.config.max_context_tokens==100000
        foreign=ToolLoopAgentRunner()
        await foreign.reset(provider=NS(provider_config={'id':'unmanaged'}),request=ProviderRequest(prompt='fixture',contexts=[]),run_context=ContextWrapper(context=None),tool_executor=NS(),agent_hooks=BaseAgentRunHooks())
        assert not isinstance(foreign.request_context_manager.truncator,LogicalTurnLimiter)
        print('PASS native reset uses complete turns only for owned work and preserves token guard')
        st=runtime['stores'][id];event.set_extra('_hosted_memory_epoch',st.reflection.epoch())
        st.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1');st.db.commit()
        before=len(requests);token=current_message_event.set(event)
        try:
            try:await ProviderOpenAIOfficial.text_chat(NS(),prompt='must not request with forgotten context',system_prompt='original persona',contexts=[])
            except asyncio.CancelledError:pass
            else:raise AssertionError('stale context started a new model request')
        finally:current_message_event.reset(token)
        assert len(requests)==before,'old memory was sent to the provider after reset'
        print('PASS forgotten foreground context is fenced before the next provider request')
        for active in (False,True):
            id='empty-'+str(active)
            settings[id]={'tenantId':'tenant','botId':id,'generation':1,'revision':1,'root':str(Path(TEMP.name)/id),'config':{'proactive':False,'behavior':{'memory':False,'qqProfile':False,'memeEnabled':False,'styleLearning':False,'fatigueEnabled':False,'burstDebounceMs':0}}}
            runtime['manage'](id,{'action':'list'})
            brain.install(id,{'tenantId':'tenant','botId':id,'generation':1,'revision':1,'epoch':1,'until':time.time()*1000+14000,'config':{'mode':'active' if active else 'passive','rollout':'live','capabilities':[]}})
            msg=AstrBotMessage();msg.type=MessageType.GROUP_MESSAGE;msg.self_id='90000000';msg.group_id='34567890';msg.session_id='34567890';msg.message_id=id;msg.sender=MessageMember('12345678','fixture');msg.message=[At(qq='90000000')];msg.message_str='';msg.raw_message={'post_type':'message'}
            event=AiocqhttpMessageEvent('',msg,PlatformMetadata('aiocqhttp','fixture',id),'34567890',bot=None)
            event.set_extra('_hosted_lease',settings[id]);event.set_extra('_hosted_generation',1)
            await PipelineScheduler.execute(NS(),event)
            request=pipeline_requests[-1]
            facts=json.loads(request.prompt)
            assert '空艾特事件' in facts.pop('meaning')
            assert facts=={'event':'mention','text':'','sender_id':'12345678','self_id':'90000000'}
            assert request.system_prompt==settings[id]['config'].get('systemPrompt','') and request.conversation.cid=='existing-session'
            assert event.get_message_str()=='' and event.get_messages()==[msg.message[0]],'input facts were mutated'
            assert request.prompt.find('你友好地询问')==-1
        print('PASS empty mention uses common pipeline, actual event facts and bound history in both modes')
        print('PASS actual hosted request preserves handoff and media prompts; passive has no handoff')
    finally:runtime['close']()

asyncio.run(main())
