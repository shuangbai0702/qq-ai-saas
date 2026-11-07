"""Actual SDK output/OneBot boundaries with a fixture protocol, never real QQ."""
import asyncio,copy,hashlib,json,os,sqlite3,sys,tempfile,time,uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from contextlib import closing
ROOT=Path(os.environ.get('FISHBOT_REVIEW_BASE',Path(__file__).resolve().parents[1]));sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
TEMP=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=TEMP.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from aiocqhttp import CQHttp
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.provider.entities import LLMResponse,TokenUsage
from behavior import install_runtime
from native_internal import WorkStopped
from autonomous_runtime import evidence_limits


async def main():
    # The fixture constructs Core directly. Production initializes this shared
    # SDK database before platform startup; do the same before concurrent sends.
    from astrbot.core import db_helper
    await db_helper.initialize();db_helper.inited=True
    bubbles=2;favorite_md5=''
    calls=[];barrier=asyncio.Event();release=asyncio.Event();mode='normal';research_requests=[];message_requests=[]
    class Provider:
        provider_config={'id':'fixture','max_context_tokens':0}
        async def text_chat(self,**kwargs):
            if mode in ('group_tool','group_tool_unknown'):
                message_requests.append(kwargs)
                if len(message_requests)==1:return LLMResponse(role='assistant',tools_call_name=['hosted_qq_group_send'],tools_call_args=[{'group_id':'34567890','text':'真实工具消息'}],tools_call_ids=['group-send'],usage=TokenUsage(input_other=50,output=10))
                return LLMResponse(role='assistant',completion_text='群里已经发了，这是内部执行说明。',usage=TokenUsage(input_other=50,output=10))
            if mode in ('favorite','favorite_cooled'):
                message_requests.append(kwargs)
                if len(message_requests)==1:
                    return LLMResponse(role='assistant',tools_call_name=['hosted_qq_favorites'],tools_call_args=[{'operation':'list'}],tools_call_ids=['actual-favorite-list'],usage=TokenUsage(input_other=50,output=10))
                if len(message_requests)==2:
                    return LLMResponse(role='assistant',tools_call_name=['hosted_qq_favorites'],tools_call_args=[{'operation':'prepare','favorite_handle':'qf_'+hashlib.sha256(b'90000000|fixture-favorite').hexdigest()[:20]}],tools_call_ids=['actual-favorite-prepare'],usage=TokenUsage(input_other=50,output=10))
                if mode=='favorite_cooled':st.social.reserve_image('private:12345678')
                return LLMResponse(role='assistant',completion_text='这张收藏[QQ_BREAK]第二条',usage=TokenUsage(input_other=50,output=10))
            if mode=='quoted':
                message_requests.append(kwargs)
                if len(message_requests)==1:
                    handle=st.db.execute('SELECT handle FROM pipeline_quote_sources').fetchone()[0]
                    return LLMResponse(role='assistant',tools_call_name=['hosted_quote_reply'],tools_call_args=[{'handle':handle}],tools_call_ids=['actual-quote'],usage=TokenUsage(input_other=50,output=10))
                return LLMResponse(role='assistant',completion_text='第一条[QQ_BREAK]第二条',usage=TokenUsage(input_other=50,output=10))
            if mode=='wrapped_text':return LLMResponse(role='assistant',completion_text='开发\n我？！\n\n别把你的代\n码搅成一锅\n饭！',usage=TokenUsage(input_other=50,output=10))
            if mode=='memory_late':barrier.set();await release.wait()
            if mode in ('self_signature','self_publish','self_signature_changed','self_publish_interrupted'):
                message_requests.append(kwargs)
                if len(message_requests)==1:return LLMResponse(role='assistant',tools_call_name=['hosted_qq_publish' if mode.startswith('self_publish') else 'hosted_qq_signature'],tools_call_args=[{'content':'真实授权的测试内容'}],tools_call_ids=['self-operation'],usage=TokenUsage(input_other=50,output=10))
                if mode=='self_publish_interrupted':brain.cancel('qq',invalidate=True);await asyncio.sleep(0)
                return LLMResponse(role='assistant',completion_text='工具结果已取得',usage=TokenUsage(input_other=50,output=10))
            if mode=='self_avatar':
                message_requests.append(kwargs)
                if len(message_requests)==1:return LLMResponse(role='assistant',tools_call_name=['hosted_qq_avatar'],tools_call_args=[{'image_handle':avatar_handle}],tools_call_ids=['self-avatar'],usage=TokenUsage(input_other=50,output=10))
                return LLMResponse(role='assistant',completion_text=json.dumps({'status':'completed','summary':'QQ avatar ACK verified','unfinished':[]}),usage=TokenUsage(input_other=50,output=10))
            if mode in ('reflect','reflect_late'):
                research_requests.append(kwargs)
                if len(research_requests)==1:return LLMResponse(role='assistant',tools_call_name=['inspect_context'],tools_call_args=[{}],tools_call_ids=['actual-read'],usage=TokenUsage(input_other=50,output=10))
                if len(research_requests)==2:
                    if mode=='reflect_late':barrier.set();await release.wait()
                    return LLMResponse(role='assistant',tools_call_name=['reflect_interest'],tools_call_args=[{'topic':'茶叶','operation':'explore','reason':'本轮真实状态读取后，选择继续了解这个主题'}],tools_call_ids=['actual-choice'],usage=TokenUsage(input_other=50,output=10))
                return LLMResponse(role='assistant',completion_text=json.dumps({'status':'completed','summary':'read actual state and chose own focus','unfinished':[]}),usage=TokenUsage(input_other=50,output=10))
            if mode in ('research','research_late'):
                research_requests.append(kwargs)
                if mode=='research_late' and len(research_requests)==1:barrier.set();await release.wait()
                if len(research_requests)==1:return LLMResponse(role='assistant',tools_call_name=['inspect_context'],tools_call_args=[{}],tools_call_ids=['read-1'],usage=TokenUsage(input_other=50,output=10))
                return LLMResponse(role='assistant',completion_text=json.dumps({'status':'completed','summary':'inspected actual companion context','unfinished':[]}),usage=TokenUsage(input_other=50,output=10))
            message_requests.append(kwargs)
            return LLMResponse(role='assistant',completion_text='第一条[QQ_BREAK]第二条' if bubbles==2 else '[QQ_BREAK]'.join('消息'+str(i+1) for i in range(bubbles)),usage=TokenUsage(input_other=50,output=10))
    bot=CQHttp()
    async def protocol(action,**params):
        calls.append((action,params))
        if action=='get_msg':return {'message_id':params['message_id'],'sender':{'user_id':12345678}}
        if action=='get_group_list':return [{'group_id':34567890,'group_name':'fixture'}]
        if mode=='group_tool_unknown' and action=='send_group_msg':return {}
        if action=='fetch_custom_face_detail':return [{'md5':favorite_md5,'resId':'fixture-favorite','url':'https://qpic.cn/fixture.gif'}]
        if action=='get_login_info':return {'user_id':90000001 if mode=='self_signature_changed' and len(calls)>1 else 90000000}
        if mode in ('late','member_late','member_unknown_late') or mode=='member_second_late' and len(calls)==2:barrier.set();await release.wait()
        if mode in ('unknown','member_unknown_late') or mode=='member_second_late' and len(calls)==2:return {}
        return {'message_id':800+len(calls)}
    bot._api=NS(call_action=protocol)
    settings={}
    core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={'platform-model':Provider()}),platform_manager=NS(get_insts=lambda:[NS(meta=lambda:PlatformMetadata('aiocqhttp','fixture','qq'),bot=bot)]))
    runtime=install_runtime(core,settings);brain=core._hosted_autonomous
    def setup(name):
        runtime['release']('qq')
        settings['qq']={'tenantId':'tenant-a','botId':'bot-a','generation':1,'revision':1,'expectedUin':'90000000','root':str(Path(TEMP.name)/name),'config':{'proactive':True,'behavior':{'enabled':True,'ownerUin':'12345678','ownerContactEnabled':True,'ownerContactIntervalMinutes':30,'ownerContactIdleMinutes':10,'memory':False,'qqProfile':False,'memeEnabled':False,'qqFaces':False,'splitMessages':True,'sendIntervalMs':0,'typingSimulation':False,'styleLearning':False,'fatigueEnabled':False,'quietStart':0,'quietEnd':0}}}
        runtime['manage']('qq',{'action':'list'})
        st=runtime['stores']['qq'];st.db.execute("INSERT INTO contacts(scope,subject,self_id,last_user,last_text) VALUES('private:12345678','12345678','90000000',?,'a genuine older message')",(time.time()-7200,));st.db.commit()
        action_id=str(uuid.uuid4());cycle=str(uuid.uuid4())
        policy={'tenantId':'tenant-a','botId':'bot-a','epoch':1,'generation':1,'revision':1,'config':{'mode':'active','rollout':'live','capabilities':['contact_owner']},'until':time.time()*1000+14000,'cycleId':cycle,'actionId':action_id}
        brain.install('qq',policy)
        identity={k:v for k,v in policy.items() if k in ('tenantId','botId','epoch','generation','revision','cycleId')};identity.update(id=action_id,principal='autonomous-service',capabilities=['contact_owner'],expiresAt=time.time()*1000+30000,budget={'modelCalls':4,'toolCalls':3,'tokens':24000,'seconds':10,'externalActions':4})
        return policy,identity,st
    try:
        mode='wrapped_text';p,w,st=setup('wrapped-output');calls.clear()
        result=await brain.execute('qq',{'work':w,'action':{'kind':'message','target':'private:12345678','objective':'reply naturally'}})
        assert result['status']=='completed' and len(calls)==2,result
        sent=[''.join(c['data']['text'] for c in params['message'] if c['type']=='text') for method,params in calls]
        assert sent==['开发我？！','别把你的代码搅成一锅饭！'],sent
        mode='normal';calls.clear()
        print('PIPELINE actual SDK delivery repairs mid-word folds and preserves two natural bubbles passed')
        p,w,st=setup('clock-rollback');wall=time.time();monotonic=time.monotonic()
        with patch('autonomous_runtime.time.time',return_value=wall-3600),patch('autonomous_runtime.time.monotonic',return_value=monotonic+20):
            assert not brain.valid('qq',w),'a wall-clock rollback extended the short effect permit'
            try:await brain.handle('qq','handoff_ack',{'epoch':p['epoch'],'id':str(uuid.uuid4()),'receipt':{'status':'accepted'}})
            except ValueError:pass
            else:raise AssertionError('expired permit accepted a late handoff acknowledgement')
        print('PIPELINE short permits expire on monotonic time even after a wall-clock rollback passed')
        p,w,st=setup('conversation-provenance');now=time.time()
        st.db.execute("UPDATE contacts SET last_user=?,last_bot=?,last_text='owner search',last_output='actual owner result link' WHERE scope='private:12345678'",(now-120,now-115))
        st.db.execute("INSERT INTO contacts(scope,subject,self_id,last_user,last_bot,last_text,last_output) VALUES('private:23456789','23456789','90000000',?,?,'ordinary friend search','search unavailable; no result')",(now-60,now-50))
        for scope,subject,event,at in [('private:12345678','12345678','owner-result',now-120),('private:23456789','23456789','friend-result',now-60)]:
            st.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed) VALUES(?,?,?,?,'message','commit','sent','',1,1)",(scope,subject,event,at))
        st.db.commit()
        brain.observe('qq',NS(get_group_id=lambda:'',get_sender_id=lambda:'23456789',get_message_str=lambda:'ordinary friend search',message_obj=NS(message_id='friend-result')),True)
        snapshot=brain.snapshot('qq');observed={c['scope']:c for c in snapshot['observedConversations']}
        assert snapshot['contacts']==[],'recent conversations incorrectly granted early contact permission'
        assert [c['scope'] for c in snapshot['deferredContacts']]==['private:12345678']
        assert snapshot['deferredContacts'][0]['notBefore']>int(now*1000),'current cooldown was hidden from planning'
        assert snapshot['identity']['ownerId']=='12345678' and snapshot['event']['owner'] is False
        assert observed['private:12345678']['owner'] is True and observed['private:12345678']['lastReply']=='actual owner result link'
        assert observed['private:23456789']['owner'] is False and observed['private:23456789']['outcome']['eventId']=='friend-result'
        assert observed['private:23456789']['lastReply']=='search unavailable; no result'
        assert len(snapshot['observedConversations'])==2 and 'event' in snapshot,'observation slot mutated the authoritative snapshot'
        st.db.execute("UPDATE contacts SET last_user=? WHERE scope='private:23456789'",(now,));st.db.commit()
        latest={c['scope']:c for c in brain.observed_conversations('qq')}
        assert latest['private:23456789']['lastReply'] is None and latest['private:23456789']['outcome'] is None,'old reply masqueraded as the result of a new in-flight request'
        assert latest['private:12345678']['lastReply']=='actual owner result link' and calls==[]
        print('PIPELINE actual conversation sources survive contact cooldown; pending requests have no stale result passed')

        p,w,st=setup('common-group-policy')
        cfg=settings['qq']['config'];cfg['behavior'].update(groupParticipationEnabled=True,groupParticipationGroups=['34567890'],groupParticipationIntervalMinutes=10,ownerContactEnabled=False)
        p['config']['capabilities']=['participate_group'];p['config']['minSleepSeconds']=600
        st.db.execute("INSERT INTO contacts(scope,subject,self_id,last_user,last_text) VALUES('group:34567890','23456789','90000000',?,'a genuine group discussion')",(time.time()-10,));st.db.commit()
        ambient=NS(get_group_id=lambda:'34567890',get_sender_id=lambda:'23456789',get_message_str=lambda:'a genuine group discussion',message_obj=NS(message_id='group-input'))
        brain.observe('qq',ambient,False)
        assert brain.events['qq']['scope']=='group:34567890','common whitelist did not wake active mode'
        assert [c['scope'] for c in brain.candidates('qq')]==['group:34567890'],'active mode added a second whitelist'
        wake=brain.snapshot('qq')['wakePolicy']
        assert set(wake)=={'idleCheckSeconds','maxSleepSeconds','quietHours'}
        assert wake['idleCheckSeconds']==600 and wake['maxSleepSeconds']==86400
        st.db.execute("UPDATE contacts SET last_user=?,last_bot=? WHERE scope='group:34567890'",(time.time()-7200,time.time()-3600));st.db.commit()
        assert brain.candidates('qq')[0]['mayInitiate'] is True,'quiet authorized group disappeared from autonomous choices'
        st.db.execute("UPDATE contacts SET last_bot=? WHERE scope='group:34567890'",(time.time(),));st.db.commit()
        cfg['behavior']['groupParticipationIntervalMinutes']=60;p['config']['minSleepSeconds']=120
        st.db.execute("INSERT INTO autonomy_groups(scope,interaction,checked) VALUES('group:34567890',?,?) ON CONFLICT(scope) DO UPDATE SET interaction=excluded.interaction,checked=excluded.checked",(time.time(),time.time()));st.db.commit()
        assert brain.candidates('qq')[0]['scope']=='group:34567890','passive inspection interval blocked outer contact'
        assert brain.snapshot('qq')['nextEventCheckSeconds']==120,'passive inspection interval leaked into outer wakes'
        assert not st.presence.ready('group:34567890',cfg,'new-passive-event',brain.provider('qq'),lambda:True,time.time()),'passive inspection lost its interval'
        calls.clear();result=await brain.execute('qq',{'work':w,'action':{'kind':'message','target':'group:34567890','objective':'join the current conversation'}})
        assert result['status']=='completed' and len(calls)==2 and all(method=='send_group_msg' for method,params in calls),result
        assert len(result['effects'])==2 and all(effect['status']=='confirmed' for effect in result['effects'])
        calls.clear()
        st.db.execute("UPDATE contacts SET opted_out=1 WHERE scope='group:34567890'");st.db.commit()
        assert brain.candidates('qq')==[],'explicit opt-out was bypassed'
        st.db.execute("UPDATE contacts SET opted_out=0 WHERE scope='group:34567890'");st.db.commit()
        with patch('initiative.quiet',return_value=True):assert brain.candidates('qq')==[],'quiet policy was bypassed'
        print('PIPELINE outer can deliver within passive inspection interval, outer wake is independent, passive inspection and explicit protections remain enforced passed')
        cfg['groups']=['45678901'];assert brain.candidates('qq')==[],'service-group restriction was bypassed'
        cfg['groups']=[];cfg['behavior']['groupParticipationGroups']=[]
        brain.events.pop('qq');brain.observe('qq',ambient,False)
        assert 'qq' not in brain.events and brain.candidates('qq')==[],'empty common whitelist granted contact'
        cfg['behavior']['groupParticipationGroups']=['34567890'];cfg['behavior']['groupParticipationEnabled']=False
        brain.observe('qq',ambient,False);assert 'qq' not in brain.events and brain.candidates('qq')==[]
        print('PIPELINE active/passive shared whitelist, service intersection, disabled gate and 600-second wake policy passed')

        for mode in ('group_tool','group_tool_unknown'):
            p,w,st=setup(mode);calls.clear();message_requests.clear()
            settings['qq']['config']['behavior'].update(groupParticipationEnabled=True,groupParticipationGroups=['34567890'])
            p['config']['capabilities']=['participate_group'];w['capabilities']=['participate_group']
            st.db.execute("INSERT INTO contacts(scope,subject,self_id,last_user,last_text) VALUES('group:34567890','23456789','90000000',?,'actual group context')",(time.time()-7200,));st.db.commit()
            data={'work':w,'action':{'kind':'message','target':'group:34567890','objective':'send the requested group message'}}
            result=await brain.execute('qq',data)
            sent=[params for method,params in calls if method=='send_group_msg']
            assert len(sent)==1,(result,calls)
            assert sent[0]['message']==[{'type':'text','data':{'text':'真实工具消息'}}],sent
            assert result['response']=='','internal final narration was recorded as a delivered reply'
            assert result['status']==('completed' if mode=='group_tool' else 'unknown'),result
            assert len(result['effects'])==1,result
            count=len(calls);await brain.execute('qq',data);assert len(calls)==count,'replay repeated a tool or final send'
        print('PIPELINE actual group tool confirmed/unknown owns same-target output; no final narration or replay delivery passed')
        mode='normal'

        for mode,kind,method in [('self_signature','profile_signature','set_self_longnick'),('self_publish','qzone_publish','send_qzone_msg')]:
            p,w,st=setup(mode);settings['qq']['expectedUin']='';settings['qq']['config']['behavior'].update(qqProfile=True,autoSignature=True,qzone=True)
            calls.clear();message_requests.clear()
            result=await brain.handle('qq','execute',{'work':w,'action':{'kind':kind,'target':'','objective':'execute actual authorized self operation'}})
            assert result['status']=='completed' and len(result['effects'])==1 and result['effects'][0]['status']=='confirmed',result
            assert [c[0] for c in calls]==['get_login_info','get_login_info',method],calls
            assert result['toolResults'][0]['detail']=='QQ协议已确认该操作回执。',result
            assert settings['qq']['expectedUin']=='','execution silently rewrote the durable identity binding'
        print('PIPELINE empty legacy expectedUin binds only the actual member protocol, self tools recheck and confirm once passed')
        mode='self_avatar';p,w,st=setup(mode);settings['qq']['config']['behavior'].update(qqProfile=True)
        import bilibili_images,io
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(16,16),'blue').save(image,format='PNG')
        asset_revision=str(settings['qq']['generation'])+':'+str(settings['qq']['revision'])
        asset=bilibili_images.record(st.db,{'execution':'completed','data':{'pic':'https://i0.hdslb.com/fixture.jpg'}},'video_info',w['id'],asset_revision)[0]
        avatar_handle=asset['handle'];st.db.execute('UPDATE bili_image_assets SET png=? WHERE handle=?',(image.getvalue(),avatar_handle));st.db.commit()
        calls.clear();message_requests.clear()
        result=await brain.handle('qq','execute',{'work':w,'action':{'kind':'research','target':'','objective':'use authorized prepared image as QQ avatar'}})
        assert result['status']=='completed' and len(result['effects'])==1 and result['effects'][0]['status']=='confirmed',result
        assert [c[0] for c in calls]==['get_login_info','set_qq_avatar'],calls
        assert calls[-1][1]['file'].startswith('base64://') and result['toolResults'][0]['detail']=='QQ协议已确认该操作回执。'
        again=await brain.handle('qq','execute',{'work':w,'action':{'kind':'research','target':'','objective':'use authorized prepared image as QQ avatar'}})
        assert [c[0] for c in calls].count('set_qq_avatar')==1 and again['status']=='completed'
        p,w,st=setup('self-avatar-shadow');settings['qq']['config']['behavior'].update(qqProfile=True);p['config']['rollout']='shadow'
        calls.clear();message_requests.clear()
        shadow=await brain.handle('qq','execute',{'work':w,'action':{'kind':'research','target':'','objective':'shadow must never set an avatar'}})
        assert shadow['status']=='blocked' and shadow['effects']==[] and not calls,shadow
        print('PIPELINE shared inner registry avatar reaches actual CQHttp effect fence once and preserves confirmed receipt passed')
        mode='self_signature_changed';p,w,st=setup(mode);settings['qq']['expectedUin']='';settings['qq']['config']['behavior'].update(qqProfile=True,autoSignature=True)
        calls.clear();message_requests.clear()
        result=await brain.handle('qq','execute',{'work':w,'action':{'kind':'profile_signature','target':'','objective':'must not execute after actual login changes'}})
        assert result['status']=='blocked' and result['effects']==[] and [c[0] for c in calls]==['get_login_info','get_login_info'],result
        assert 'QQ账号已变化' in result['toolResults'][0]['detail'],result
        print('PIPELINE actual identity changed between binding and final tool check rejects self operation passed')
        mode='self_publish_interrupted';p,w,st=setup(mode);settings['qq']['config']['behavior'].update(qzone=True)
        calls.clear();message_requests.clear()
        result=await brain.handle('qq','execute',{'work':w,'action':{'kind':'qzone_publish','target':'','objective':'publish once and preserve the real ACK after interruption'}})
        assert result['status']=='partial' and len(result['effects'])==1 and result['effects'][0]['status']=='confirmed',result
        assert '不得重复执行' in result['summary'] and '不可重复执行' in result['unfinished'][0] and result['stopReason'],result
        again=await brain.handle('qq','receipt',{'id':w['id']})
        assert again['result']['status']=='partial' and [c[0] for c in calls].count('send_qzone_msg')==1,again
        print('PIPELINE interrupted computation preserves confirmed self effect as partial, retains stop reason and never repeats delivery passed')
        mode='normal';calls.clear();message_requests.clear()

        p,w,st=setup('actual-tool-catalog')
        initial=brain.snapshot('qq')
        with patch.object(brain,'candidates',side_effect=AssertionError('ledger poll built retired planner context')):
            ledger=await brain.handle('qq','snapshot',{'ledgerOnly':True})
            assert set(ledger)=={'pendingHandoffs','foregroundFeedback','event','nextEventCheckSeconds'}
        assert 'bilibili_search' not in initial['availableTools']
        assert next(t for t in initial['innerTools'] if t['name']=='bilibili_search')['code']=='feature_disabled'
        p['config']['capabilities']=['research','contact_owner','profile_signature','qzone_publish'];w['capabilities']=list(p['config']['capabilities'])
        cfg=settings['qq']['config'];cfg['behavior'].update(qqProfile=True,autoSignature=True,qzone=True,memory=True,bilibiliSearchEnabled=True)
        snapshot=brain.snapshot('qq');catalog={c['kind']:c for c in snapshot['capabilityCatalog']}
        assert all(catalog[k]['available'] for k in ('research','message','profile_signature','qzone_publish'))
        for kind in catalog:
            identity={**w,'kind':kind};scope='private:12345678' if kind=='message' else 'self'
            event=brain.make_event('qq',identity,scope if scope!='self' else 'private:0','metadata check','90000000');event.set_extra('_hosted_autonomous_action',{'kind':kind})
            assert catalog[kind]['tools']==[t.name for t in brain.task_tools('qq',identity,scope,event)],'outer and actual inner tool assembly diverged'
        descriptions={t['name']:t for t in snapshot['innerTools']}
        assert 'hosted_qq_publish' in snapshot['availableTools'] and 'hosted_qq_signature' in snapshot['availableTools']
        assert '自主空间任务' in descriptions['hosted_qq_publish']['description']
        assert descriptions['hosted_qq_publish']['parameters']['required']==['content']
        assert 'hosted_qq_avatar' in descriptions
        import inner_tools
        original_features=inner_tools.features
        from astrbot.core.agent.tool import FunctionTool
        extension=FunctionTool(name='future_inner_capability',description='fixture registered only at the common inner assembly',parameters={'type':'object','properties':{}})
        def with_extension(*args,**kwargs):return original_features(*args,**kwargs)+[extension]
        with patch.object(inner_tools,'features',with_extension):
            extended=brain.snapshot('qq')
            assert any(t['name']=='future_inner_capability' for t in extended['innerTools'])
            assert extended['capabilityRevision']!=snapshot['capabilityRevision']
        assert brain.snapshot('qq')['capabilityRevision']==snapshot['capabilityRevision']
        cfg['behavior']['bilibiliSearchEnabled']=False
        disabled=brain.snapshot('qq')
        assert 'bilibili_search' not in disabled['availableTools']
        assert next(t for t in disabled['innerTools'] if t['name']=='bilibili_search')['code']=='feature_disabled'
        for kind in catalog:
            identity={**w,'kind':kind};scope='private:12345678' if kind=='message' else 'self'
            event=brain.make_event('qq',identity,scope if scope!='self' else 'private:0','metadata check','90000000');event.set_extra('_hosted_autonomous_action',{'kind':kind})
            assert 'bilibili_search' not in [t.name for t in brain.task_tools('qq',identity,scope,event)]
        cfg['behavior']['bilibiliSearchEnabled']=True
        cfg['behavior']['qqProfile']=False;catalog={c['kind']:c for c in brain.snapshot('qq')['capabilityCatalog']}
        assert not catalog['profile_signature']['available'] and 'hosted_qq_signature' not in catalog['profile_signature']['tools']
        p['config']['rollout']='shadow';catalog={c['kind']:c for c in brain.snapshot('qq')['capabilityCatalog']}
        assert not catalog['qzone_publish']['available'] and catalog['research']['available']
        p['config']['capabilities']=['contact_owner'];snapshot=brain.snapshot('qq')
        catalog={c['kind']:c for c in snapshot['capabilityCatalog']}
        assert catalog['research']['authorized'] and catalog['qzone_publish']['authorized'],'outer vocabulary must not control inner features'
        assert not catalog['profile_signature']['authorized'],'disabled inner feature must stay disabled'
        assert any(t['name']=='hosted_qq_publish' for t in snapshot['innerTools'])
        assert calls==[],'capability enumeration executed an external tool'
        print('PIPELINE outer capability catalog matches actual inner tool factories, permissions, settings and shadow gates without effects passed')

        p,w,st=setup('directed-observation-ack')
        settings['qq']['config']['behavior'].update(groupParticipationEnabled=True,groupParticipationGroups=['34567890'])
        p['config']['capabilities'].append('participate_group')
        directed=NS(get_group_id=lambda:'34567890',get_sender_id=lambda:'12345678',get_message_str=lambda:'actual directed input',message_obj=NS(message_id='directed-ack'),get_extra=lambda key,default=None:default)
        ambient=NS(get_group_id=lambda:'34567890',get_sender_id=lambda:'23456789',get_message_str=lambda:'ambient discussion',message_obj=NS(message_id='ambient-ack'))
        brain.observe('qq',directed,True);first=brain.events['qq']['at']
        brain.observe('qq',ambient,False)
        assert brain.events['qq']['eventId']=='directed-ack' and brain.pending_ambient['qq']['eventId']=='ambient-ack'
        brain.install('qq',{**p,'observedThrough':first})
        assert brain.events['qq']['eventId']=='directed-ack','incoming ACK discarded the pending foreground result'
        work=NS(observes_input=True,ledger_owner=True,event_id='directed-ack',scope='group:34567890',subject='12345678',status='sent',confirmed_messages=1,attempted=1,confirmed=1,reason='')
        brain.completed('qq',directed,work);finished=brain.events['qq']['at']
        brain.observe('qq',ambient,False)
        brain.install('qq',{**p,'observedThrough':first})
        assert brain.events['qq']['phase']=='foreground_result','ambient traffic discarded an uncommitted execution result'
        brain.install('qq',{**p,'observedThrough':finished})
        assert brain.events['qq']['eventId']=='ambient-ack','durable result ACK did not release newer ambient observation'
        brain.install('qq',{**p,'observedThrough':0})
        assert brain.policies['qq']['observedThrough']==finished,'an old cycle policy regressed the durable observation ACK'
        assert brain.snapshot('qq')['feedbackVersion']==finished,'ambient promotion erased the latest actual inner feedback'
        assert not brain.valid('qq',w),'an old feedback version retained execution permission'
        saved_feedback=brain.snapshot('qq')['foregroundFeedback'];new_cycle=str(uuid.uuid4())
        fresh_work={**w,'cycleId':new_cycle,'feedbackVersion':finished}
        brain.release('qq');brain.install('qq',{**p,'cycleId':new_cycle,'latestFeedback':saved_feedback})
        assert brain.valid('qq',fresh_work),'persisted feedback policy could not restore the freshness fence'
        assert brain.snapshot('qq')['foregroundFeedback']==saved_feedback
        brain.release('qq');assert 'qq' not in brain.pending_ambient and 'qq' not in brain.events
        assert 'qq' not in brain.feedbacks
        print('PIPELINE directed input and actual result survive ambient traffic until durable ACK; stale policy and release passed')

        p,w,st=setup('ambient-execution-feedback')
        settings['qq']['config']['behavior'].update(groupParticipationEnabled=True,groupParticipationGroups=['34567890'])
        ambient=NS(get_group_id=lambda:'34567890',get_sender_id=lambda:'23456789',get_message_str=lambda:'normal group traffic',message_obj=NS(message_id='ambient-waiting'),get_extra=lambda key,default=None:default)
        from bot_pipeline import Work
        work=Work('bot-a','group:34567890','23456789','90000000','ambient-waiting','message',time.time(),settings['qq'],ledger_owner=True)
        work.stage('admission');work.stop('autonomous_owner_observing','waiting')
        brain.observe('qq',ambient,False);brain.completed('qq',ambient,work)
        assert brain.snapshot('qq')['feedbackVersion']==0 and brain.valid('qq',w),'mere ambient admission canceled existing autonomous work'
        assert brain.events['qq']['eventId']=='ambient-waiting' and brain.events['qq'].get('phase')!='foreground_result'
        work.status='silent';work.reason='model_chose_silence';work.stage('model_tools')
        brain.completed('qq',ambient,work)
        assert brain.snapshot('qq')['feedbackVersion']>0 and not brain.valid('qq',w),'a genuine model result failed to invalidate stale planning'
        print('PIPELINE ambient admission stays observable without canceling work; actual silent model feedback still advances freshness passed')

        # The durable experience ledger stays per member: install() feeds it,
        # and each scope only ever sees its own sourced records.
        p,w,other=setup('shared-isolated-member');settings['qq']['config']['behavior']['memory']=True
        history={'sampledAt':int(time.time()*1000),'decisions':[{'cycleId':'real-cycle','at':None,'reason':'historical time unknown','action':None,'wait':{'kind':'time','seconds':600}}],
                 'actions':[{'id':'self-search','kind':'research','target':'','sourceScope':None,'objective':'public research','status':'blocked','toolResults':[{'tool':'bilibili_search','status':'unavailable'}],'effects':[]},
                            {'id':'other-private','kind':'research','target':'','sourceScope':'private:23456789','objective':'confidential other request','status':'completed'}]}
        settings['qq']['config']['behavior']['memory']=True
        brain.install('qq',{**p,'experience':history})
        from bot_pipeline import experience_context
        owner=experience_context(other,'private:12345678',settings['qq']['config']['behavior'])
        group=experience_context(other,'group:34567890',settings['qq']['config']['behavior'])
        assert [a['id'] for a in owner['actions']]==['self-search'] and group['actions']==[]
        assert owner['actions'][0]['status']=='blocked' and owner['decisions'][0]['at'] is None
        assert 'reason' not in group['decisions'][0],'private decision reasoning leaked into a group'
        internal=brain.make_event('qq',w,'private:12345678','a new local result','90000000')
        local_work=other.pipeline.bind_internal(internal,settings['qq'],w,'self','a new local result')
        other.pipeline.finish_internal(local_work,{'status':'blocked','task':{'kind':'research','target':'','objective':'fresh actual failure'},'summary':'actual failure before PG refresh','effects':[],'toolResults':[{'tool':'bilibili_search','status':'failed'}]})
        latest=experience_context(other,'private:12345678',settings['qq']['config']['behavior'])
        assert latest['actions'][0]['id']==w['id'] and latest['actions'][0]['status']=='blocked'
        # A research task belongs to its source conversation even without a
        # send target. A legacy local receipt must not erase durable provenance.
        other.autonomous_experience['actions'].append({'id':w['id'],'kind':'research','target':'','sourceScope':'group:34567890','objective':'trusted group research','status':'executing'})
        group=experience_context(other,'group:34567890',settings['qq']['config']['behavior'])
        assert group['actions'][0]['id']==w['id'] and group['actions'][0]['sourceScope']=='group:34567890'
        assert all(a['id']!=w['id'] for a in experience_context(other,'group:99999999',settings['qq']['config']['behavior'])['actions'])
        assert all(a['id']!=w['id'] for a in experience_context(other,'private:12345678',settings['qq']['config']['behavior'])['actions'])
        other.autonomous_experience['actions'][-1].update(status='completed',finishedAt=int(time.time()*1000),summary='durable final result')
        final=experience_context(other,'group:34567890',settings['qq']['config']['behavior'])['actions'][0]
        assert final['status']=='completed' and final['summary']=='durable final result'
        assert brain.valid('qq',w),'experience synchronization incorrectly invalidated autonomous work'
        settings['qq']['config']['behavior']['memory']=False
        assert experience_context(other,'private:12345678',settings['qq']['config']['behavior'])['actions']==[]
        runtime['release']('qq');p,w,other=setup('shared-restarted-member');settings['qq']['config']['behavior']['memory']=True;brain.install('qq',{**p,'experience':history})
        assert experience_context(other,'private:12345678',settings['qq']['config']['behavior'])['actions'][0]['id']=='self-search'
        print('PIPELINE shared actual decisions/failures restore from policy, local receipts are immediate, private origins stay isolated, history does not cancel work passed')

        mode='normal';research_requests.clear()
        p,w,st=setup('foreground-handoff');extras={}
        assert not brain.owns('qq'),'autonomous ownership must not suppress the legacy proactive sweep in any mode'
        try:await brain.handle('qq','think',{'work':w})
        except ValueError:pass
        else:raise AssertionError('the outer planning think operation must be gone')
        incoming=NS(get_group_id=lambda:'',get_sender_id=lambda:'23456789',get_self_id=lambda:'90000000',get_extra=lambda key,default=None:extras.get(key,default),set_extra=lambda key,value:extras.__setitem__(key,value),message_obj=NS(message_id='handoff-event',timestamp=time.time()))
        incoming_work=st.pipeline.bind(incoming,settings['qq']);brain.observe('qq',incoming,True)
        propose=next(t for t in brain.foreground_tools('qq',incoming,lambda:True) if t.name=='propose_background_task')
        assert 'pending表示等待后台登记确认' in brain.foreground_prompt('qq',incoming)
        assert '外层' not in brain.foreground_prompt('qq',incoming),'stale outer-layer wording must not return'
        brain.completed('qq',incoming,incoming_work)
        assert brain.events['qq']['result']['taskHandoff'] is None,'ordinary promise masqueraded as registered work'
        submitted=json.loads(await propose.call(None,'read my real state later',delay_seconds=300))
        assert submitted['state']=='pending' and submitted['queued'] and calls==[]
        again=json.loads(await propose.call(None,'read my real state later',delay_seconds=300))
        assert again['requestId']==submitted['requestId']
        from bot_pipeline import BotPipeline
        reopened=BotPipeline(st.db);pending=reopened.pending_handoffs(p['epoch'])
        assert len(pending)==1 and pending[0]['senderId']=='23456789' and pending[0]['scope']=='private:23456789' and 'owner' not in pending[0]
        conflict=json.loads(await propose.call(None,'silently replace another task',delay_seconds=300))
        assert conflict['queued'] is False and len(reopened.pending_handoffs(p['epoch']))==1
        await brain.handle('qq','handoff_ack',{'epoch':p['epoch'],'id':submitted['requestId'],'receipt':{'status':'accepted','goalId':submitted['requestId']}})
        assert reopened.pending_handoffs(p['epoch'])==[]
        assert json.loads(await propose.call(None,'read my real state later',delay_seconds=300))['state']=='accepted'
        brain.completed('qq',incoming,incoming_work)
        assert brain.events['qq']['result']['taskHandoff']['state']=='accepted'
        print('PIPELINE durable handoff survives reopen, deduplicates and keeps actual sender; no local timer or QQ send passed')

        # A real accepted task may return its result while proactive contact
        # is disabled/cooling down. Permission remains bound to that one goal.
        row=st.db.execute('SELECT data FROM pipeline_handoffs WHERE id=?',(submitted['requestId'],)).fetchone();request_data=json.loads(row[0]);request_data['at']-=301000;st.db.execute('UPDATE pipeline_handoffs SET data=? WHERE id=?',(json.dumps(request_data),submitted['requestId']));st.db.commit()
        calls.clear();settings['qq']['config']['proactive']=False
        st.db.execute("INSERT INTO contacts(scope,subject,self_id,last_user,last_text) VALUES('private:23456789','23456789','90000000',?,'actual task input')",(time.time(),));st.db.commit()
        source={'handoffId':submitted['requestId'],'eventId':'handoff-event','scope':'private:23456789','senderId':'23456789','owner':False}
        p={**p,'cycleId':str(uuid.uuid4()),'actionId':str(uuid.uuid4()),'until':time.time()*1000+14000};brain.install('qq',p)
        w={**w,'id':p['actionId'],'cycleId':p['cycleId'],'feedbackVersion':brain.snapshot('qq')['feedbackVersion'],'expiresAt':time.time()*1000+30000}
        assert brain.candidates('qq')==[]
        granted=st.pipeline.request_reply(p,submitted['requestId'],source)
        assert granted and granted['scope']=='private:23456789','accepted handoff lost its scoped reply permit'
        forged={**source,'senderId':'12345678'}
        assert st.pipeline.request_reply(w,submitted['requestId'],forged,w['id']) is None
        data={'work':w,'action':{'kind':'message','goalId':submitted['requestId'],'target':'private:23456789','objective':'deliver the actual requested result','requestSource':source}}
        reply=await brain.handle('qq','execute',data)
        assert reply['status']=='completed' and len(calls)==2,reply
        assert all(c[1]['user_id']==23456789 for c in calls)
        assert st.pipeline.request_reply(p,submitted['requestId'],source) is None,'consumed reply permission still queries as grantable'
        assert (await brain.handle('qq','execute',data))['effects']==reply['effects'] and len(calls)==2
        p={**p,'actionId':str(uuid.uuid4()),'until':time.time()*1000+14000};brain.install('qq',p)
        repeated=await brain.handle('qq','execute',{**data,'work':{**w,'id':p['actionId']}})
        assert repeated['status']=='blocked' and len(calls)==2,'a second action reused consumed reply permission'
        assert st.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]==0,'solicited task result consumed proactive daily quota'
        assert st.db.execute("SELECT unanswered FROM contacts WHERE scope='private:23456789'").fetchone()[0]==0
        print('PIPELINE accepted request result bypasses only proactive cooldown, stays scoped, uses real receipts and cannot resend passed')
        calls.clear()

        p,w,st=setup('normal');data={'work':w,'action':{'kind':'message','target':'private:12345678','objective':'接续真实话题'}}
        result=await brain.handle('qq','execute',data)
        assert result['status']=='completed',result
        assert len(calls)==2 and all(c[0]=='send_private_msg' and c[1]['user_id']==12345678 for c in calls),calls
        assert [e['receipt'] for e in result['effects']]==['801','802'],result
        assert st.db.execute("SELECT count(*) FROM relations WHERE subject='0'").fetchone()[0]==0,'service author gained a human relationship'
        again=await brain.handle('qq','execute',data);assert again['effects']==result['effects'] and len(calls)==2,'duplicate sent again'
        changed=copy.deepcopy(data);changed['action']['objective']='a different action'
        try:await brain.handle('qq','execute',changed)
        except ValueError:pass
        else:raise AssertionError('same action ID accepted different content')
        print('PIPELINE actual receipts / service identity / duplicate fence passed')

        for index,prior in enumerate([
            {'status':'blocked','summary':'old target rejected','contextScope':'12345678'},
            {'status':'blocked','summary':'old same-scope task rejected','contextScope':'private:12345678'},
            {'status':'blocked','summary':'old unscoped failure'},
            {'status':'completed','summary':'actual internal read','contextScope':'self'},
        ]):
            calls.clear();message_requests.clear();p,w,st=setup('feedback-scope-'+str(index))
            result=await brain.handle('qq','execute',{'work':w,'action':data['action'],'feedback':prior})
            assert result['status']=='completed' and len(calls)==2,result
            message=next(m for m in reversed(message_requests[0]['contexts']) if m.role=='user')
            content=message.content if isinstance(message.content,str) else ''.join(p.text for p in message.content if p.type=='text')
            payload=json.loads(content)
            assert payload['priorFeedback']==(prior if prior.get('contextScope')=='self' else None),'historical delivery failure replaced current admission'
        print('PIPELINE new message does not inherit a former dispatch failure passed')

        calls.clear();mode='research';p,w,st=setup('research')
        settings['qq']['config']['behavior']['memory']=True
        p.update(epoch=2,config={**p['config'],'capabilities':['research']});w.update(epoch=2,capabilities=['research'])
        brain.install('qq',p)
        original_context=brain.prepare_context;research_context=[]
        def capture_context(*args):
            value=original_context(*args);research_context.append(value);return value
        brain.prepare_context=capture_context
        try:result=await brain.handle('qq','execute',{'work':w,'action':{'kind':'research','objective':'inspect the actual persisted companion context'}})
        finally:brain.prepare_context=original_context
        assert result['status']=='completed' and len(research_requests)==2 and result['evidence'][0]['source']=='existing_companion_state',result
        assert result['evidenceLimits']==['只证明读取了当前人物状态，不是外部事实核验。'],result
        assert evidence_limits([{'source':'bilibili_public_search'}])==['仅检索元数据；未读取原文或媒体，不能据此验证内容细节。']
        assert evidence_limits([{'source':'new_undeclared_source'}])==['证据源能力范围未经声明，不应推断已验证其内容。']
        assert calls==[] and 'social' not in research_context[0] and research_context[0]['identity']['sender_id']=='0'
        assert 'state' in research_context[0] and 'companion' in research_context[0]
        remembered=st.memory.recall('self','0','inspect actual persisted companion context',time.time())
        assert any(x['origin']=='autonomous_result' and 'research_result_summary_not_verified_facts' in x['value'] for x in remembered),remembered
        assert json.loads(remembered[0]['value'])['evidenceLimits']==result['evidenceLimits']
        assert not st.memory.recall('private:12345678','12345678','inspect actual persisted companion context',time.time()),'private self research leaked into a human profile'
        again=await brain.handle('qq','execute',{'work':w,'action':{'kind':'research','objective':'inspect the actual persisted companion context'}})
        assert len(research_requests)==2 and again['status']=='completed'
        assert st.db.execute("SELECT count(*) FROM facts WHERE scope='self' AND subject='0'").fetchone()[0]==1,'duplicate action created a second experience'
        # Turning memory off is authoritative even for successful research.
        settings['qq']['config']['behavior']['memory']=False
        p={**p,'actionId':str(uuid.uuid4())};w={**w,'id':p['actionId']};brain.install('qq',p)
        research_requests.clear()
        result=await brain.handle('qq','execute',{'work':w,'action':{'kind':'research','objective':'inspect another context with memory disabled'}})
        assert result['status']=='completed' and st.db.execute("SELECT count(*) FROM facts WHERE scope='self' AND subject='0'").fetchone()[0]==1,'disabled memory stored a new experience'
        settings['qq']['config']['behavior']['memory']=True
        assert brain.snapshot('qq')['selfExperiences'][0]['origin']=='autonomous_result'
        mode='research_late';barrier=asyncio.Event();release=asyncio.Event();research_requests.clear()
        p={**p,'actionId':str(uuid.uuid4()),'until':time.time()*1000+14000};w={**w,'id':p['actionId']};brain.install('qq',p)
        task=asyncio.create_task(brain.handle('qq','execute',{'work':w,'action':{'kind':'research','objective':'read while the user resets memory'}}))
        await asyncio.wait_for(barrier.wait(),5)
        st.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1');st.db.commit()
        release.set();result=await asyncio.wait_for(task,5)
        assert result['status']=='canceled' and result['stopReason']=='internal_work_stale' and len(research_requests)==1,result
        assert st.db.execute("SELECT count(*) FROM facts WHERE scope='self' AND subject='0'").fetchone()[0]==1,'late research restored memory after a reset'
        print('PIPELINE read-only research uses shared identity/memory, actual tool evidence and no QQ send passed')

        calls.clear();mode='memory_late';barrier=asyncio.Event();release=asyncio.Event();p,w,st=setup('memory-reply-reset')
        settings['qq']['config']['behavior']['memory']=True
        task=asyncio.create_task(brain.handle('qq','execute',{'work':w,'action':data['action']}))
        await asyncio.wait_for(barrier.wait(),5)
        st.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1');st.db.commit();release.set()
        result=await asyncio.wait_for(task,5)
        assert calls==[] and result['status']=='blocked' and result['stopReason']=='internal_work_stale',result
        assert st.db.execute('SELECT count FROM deliveries').fetchone()[0]==0,'canceled old-memory reply retained its send reservation'
        replay=await brain.handle('qq','execute',{'work':w,'action':data['action']})
        assert replay['stopReason']=='internal_work_stale' and calls==[],'replay sent the canceled old-memory reply'
        print('PIPELINE memory reset stops an in-flight reply before tools/output and releases its reservation passed')

        mode='normal';calls.clear();p,w,st=setup('memory-context-canceled')
        with patch('execution_memory.context',side_effect=asyncio.CancelledError('controlled context cancellation')):
            result=await brain.handle('qq','execute',{'work':w,'action':data['action']})
        assert calls==[] and result['stopReason']=='task_canceled',result
        assert st.db.execute('SELECT count FROM deliveries').fetchone()[0]==0,'canceled context leaked its send reservation'
        assert st.pipeline.internal_result(w['id'])['result']['stopReason']=='task_canceled','canceled context left unclosed work'
        print('PIPELINE context cancellation settles Work and refunds an unstarted send passed')

        mode='reflect';research_requests.clear();calls.clear();p,w,st=setup('self-reflection')
        settings['qq']['config']['behavior']['memory']=True
        p.update(epoch=2,config={**p['config'],'capabilities':['research']});w.update(epoch=2,capabilities=['research']);brain.install('qq',p)
        reflection_action={'kind':'research','objective':'read the current self context and decide whether a focus is worth exploring'}
        result=await brain.handle('qq','execute',{'work':w,'action':reflection_action})
        assert result['status']=='completed' and result['usage']['toolCalls']==2 and calls==[],result
        row=st.db.execute("SELECT hits FROM companion_interests WHERE scope='self' AND topic='茶叶'").fetchone();assert row[0]==1
        assert any(i['topic']=='茶叶' and i['source']=='autonomous_choice' for i in brain.snapshot('qq')['self']['interests'])
        import companion_state
        assert any(i['source']=='autonomous_choice' and i['source_scope']=='self' for i in companion_state.context(st.db,settings['qq']['config'],'private:12345678',time.time())['interests'])
        await brain.handle('qq','execute',{'work':w,'action':reflection_action})
        assert st.db.execute('SELECT count(*) FROM companion_reflections').fetchone()[0]==1 and len(research_requests)==3,'replayed Work reflected twice'
        check={**w,'kind':'research'}
        assert any(t.name=='reflect_interest' for t in brain.tools('qq',check,'self'))
        assert any(t.name=='reflect_interest' for t in brain.tools('qq',{**w,'kind':'message'},'private:12345678'))
        settings['qq']['config']['behavior']['memory']=False
        assert all(t.name!='reflect_interest' for t in brain.tools('qq',check,'self'))
        settings['qq']['config']['behavior']['memory']=True;settings['qq']['config']['behavior']['selfDevelopment']=False
        assert all(t.name!='reflect_interest' for t in brain.tools('qq',check,'self'))
        settings['qq']['config']['behavior']['selfDevelopment']=True
        p={**p,'epoch':3,'config':{**p['config'],'rollout':'shadow'}};w={**w,'epoch':3};brain.install('qq',p)
        assert all(t.name!='reflect_interest' for t in brain.tools('qq',{**w,'kind':'research'},'self'))
        print('PIPELINE actual-read self reflection is scoped, idempotent, optional and absent from chat/shadow passed')

        mode='reflect_late';research_requests.clear();barrier=asyncio.Event();release=asyncio.Event();p,w,st=setup('self-reflection-reset')
        settings['qq']['config']['behavior']['memory']=True
        p.update(epoch=2,config={**p['config'],'capabilities':['research']});w.update(epoch=2,capabilities=['research']);brain.install('qq',p)
        task=asyncio.create_task(brain.handle('qq','execute',{'work':w,'action':reflection_action}))
        await asyncio.wait_for(barrier.wait(),5);st.db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1');st.db.commit();release.set()
        result=await asyncio.wait_for(task,5)
        assert st.db.execute("SELECT count(*) FROM companion_interests WHERE source='autonomous_choice'").fetchone()[0]==0,'late reflection rewrote state after a reset'
        print('PIPELINE memory reset fences a delayed native self-reflection tool passed')

        calls.clear();mode='unknown';p,w,st=setup('unknown')
        result=await brain.handle('qq','execute',{'work':w,'action':data['action']})
        assert result['status']=='unknown' and len(calls)==1,result
        assert result['effects'][0]['status']=='unknown',result
        assert st.db.execute('SELECT count FROM deliveries').fetchone()[0]==1,'unknown was incorrectly refunded'
        print('PIPELINE missing message ID stays unknown; no second bubble passed')

        calls.clear();mode='late';barrier=asyncio.Event();release=asyncio.Event();p,w,st=setup('late')
        task=asyncio.create_task(brain.handle('qq','execute',{'work':w,'action':data['action']}))
        await asyncio.wait_for(barrier.wait(),5)
        brain.install('qq',{**p,'epoch':2,'config':{**p['config'],'mode':'passive'}})
        release.set();result=await asyncio.wait_for(task,5)
        assert len(calls)==1 and result['status']=='partial',result
        assert result['effects'][0]['status']=='confirmed','real late ack was lost'
        assert not brain.owns('qq'),'passive mode retained autonomous owner'
        print('PIPELINE hot switch keeps late real receipt and cancels remaining output passed')

        for mode in ('member_late','member_unknown_late','member_second_late'):
            calls.clear();barrier=asyncio.Event();release=asyncio.Event();p,w,st=setup(mode)
            other_settings={**settings['qq'],'tenantId':'tenant-b','botId':'bot-b','root':str(Path(TEMP.name)/('other-'+mode))};settings['other']=other_settings
            core.provider_manager.inst_map.update({'provider-qq':Provider(),'provider-other':Provider()})
            other_policy={**p,'tenantId':'tenant-b','botId':'bot-b'};brain.install('other',other_policy)
            other_work={**w,'tenantId':'tenant-b','botId':'bot-b'}
            task=asyncio.create_task(brain.handle('qq','execute',{'work':w,'action':data['action']}))
            reached=asyncio.create_task(barrier.wait());done,_=await asyncio.wait((task,reached),timeout=5,return_when=asyncio.FIRST_COMPLETED)
            assert reached in done,task.result() if task.done() else 'protocol barrier was not reached'
            settings.pop('qq');brain.release('qq');draining=asyncio.create_task(brain.drain('qq'));release.set()
            result=await asyncio.wait_for(task,5);await asyncio.wait_for(draining,5)
            expected='unknown' if mode=='member_unknown_late' else 'partial';count=2 if mode=='member_second_late' else 1
            assert len(calls)==count and result['status']==expected,result
            assert result.get('response')=='第一条[QQ_BREAK]第二条','member unload lost the message content separately from its transport summary'
            assert len(result['effects'])==count and result['effects'][0]['id']==w['id']+':0',result
            assert result['effects'][0]['status']==('unknown' if expected=='unknown' else 'confirmed') and result['effects'][0]['receipt']==(None if expected=='unknown' else '801'),result
            if count==2:assert result['effects'][1]['status']=='unknown' and result['effects'][1]['receipt'] is None,result
            assert st.pipeline.internal_result(w['id'])['result']['effects']==result['effects'],'late receipt was not durably settled before store close'
            assert brain.valid('other',other_work),'unloading one member invalidated another tenant'
            assert 'qq' not in brain.running and 'qq' not in brain.tasks
            runtime['release']('qq')
            with closing(sqlite3.connect(str(Path(TEMP.name)/mode/'behavior.sqlite'))) as reopened:
                saved=json.loads(reopened.execute('SELECT result FROM pipeline_actions WHERE id=?',(w['id'],)).fetchone()[0])
                assert saved['status']==expected and saved['effects']==result['effects'],'closing and reopening lost the original effect'
                assert saved['response']==result['response'],'closing and reopening lost the original message content'
            runtime['release']('other');settings.pop('other')
        print('PIPELINE member unload preserves a late confirmed receipt, drains once and leaves the other tenant valid passed')

        mode='quoted';p,w,st=setup('internal-quote');calls.clear();message_requests.clear()
        import quote_reply
        observed=NS(message_obj=NS(message_id=42),get_sender_id=lambda:'12345678',get_self_id=lambda:'90000000',get_message_str=lambda:'actual observed message')
        quote_reply.observe(st.db,observed,settings['qq'],'private:12345678')
        result=await brain.execute('qq',{'work':w,'action':{'kind':'message','target':'private:12345678','objective':'quote the actual observed message'}})
        assert result['status']=='completed',result
        assert calls[0]==('get_msg',{'message_id':42}),{'calls':calls,'result':result,'requests':len(message_requests)}
        sent=[params['message'] for action,params in calls if action=='send_private_msg']
        assert len(sent)==2 and sent[0][0]['type']=='reply' and str(sent[0][0]['data']['id'])=='42',sent
        assert not any(c['type']=='reply' for c in sent[1]),sent
        event=brain.make_event('qq',w,'private:12345678','untrusted arbitrary query','90000000')
        from astrbot.core.platform.astr_message_event import current_message_event
        token=current_message_event.set(event)
        try:
            try:await event.bot.get_msg(message_id=43)
            except ValueError as error:assert str(error)=='internal quote read denied'
            else:raise AssertionError('unselected internal message read was allowed')
        finally:current_message_event.reset(token)
        print('PIPELINE internal quote verifies only selected observed source and routes reply on first bubble passed')

        import io,qq_favorite_tools
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(3,3),'blue').save(image,format='GIF',save_all=True,append_images=[Image.new('RGB',(3,3),'red')],duration=100,loop=0);favorite_md5=hashlib.md5(image.getvalue()).hexdigest()
        for mode in ('favorite','favorite_cooled'):
            p,w,st=setup('internal-'+mode);calls.clear();message_requests.clear()
            settings['qq']['config']['behavior']['memeEnabled']=True
            settings['qq']['config']['behavior']['memePacks']=[] # Prove the live QQ tool works without platform/cache candidates.
            assert st.social.context('private:12345678','发收藏',settings['qq']['config']['behavior'])['meme_candidates']==[]
            with patch.object(qq_favorite_tools,'download',return_value=image.getvalue()):
                result=await brain.execute('qq',{'work':w,'action':{'kind':'message','target':'private:12345678','objective':'发收藏'}})
            assert result['status']=='completed',result
            sent=[params['message'] for method,params in calls if method=='send_private_msg']
            images=[c for chain in sent for c in chain if c['type']=='image']
            assert len(images)==(1 if mode=='favorite' else 0),(mode,images,result)
            if images:assert images[0]['data'].get('sub_type')==1,images
            assert len(sent)==2,sent
        print('PIPELINE real QQ favorites do not depend on platform candidates and final cooldown still fences concurrent output passed')

        for bubbles in (1,2,3,4):
            mode='normal';p,w,st=setup('logical-budget-'+str(bubbles));w['budget']['externalActions']=1;calls.clear()
            result=await brain.execute('qq',{'work':w,'action':{'kind':'message','target':'private:12345678','objective':'one logical reply'}})
            assert result['status']=='completed' and len(calls)==bubbles,result
            assert result['actionUnitVersion']==2 and len({e['logicalActionId'] for e in result['effects']})==1,result
        bubbles=2
        print('PIPELINE one logical action delivers all 1-4 bubbles with independent receipts passed')
        from astrbot.api.event import MessageChain
        from astrbot.core.message.components import Plain
        from astrbot.core.platform.astr_message_event import current_message_event
        p,w,st=setup('foreground-no-message-id');mode='unknown';calls.clear()
        event=brain.make_event('qq',w,'private:12345678','real event fixture','90000000')
        event.message_obj.sender.user_id='12345678';event.message_obj.message_id='foreground-without-receipt'
        event.set_extra('_hosted_internal_work',None);event.set_extra('_hosted_initiative',False)
        work=st.pipeline.bind(event,settings['qq']);token=current_message_event.set(event)
        try:
            try:await event.send(MessageChain([Plain('fixture reply')]))
            except RuntimeError as error:assert str(error)=='missing_protocol_receipt'
            else:raise AssertionError('empty OneBot response was accepted as delivery')
        finally:current_message_event.reset(token)
        st.pipeline.finish(work,{})
        assert work.status=='unknown' and work.attempted==1 and work.confirmed==0 and work.confirmed_messages==0
        assert not event.get_extra('_hosted_protocol_receipts')
        print('PIPELINE foreground missing message ID stays unknown and never commits a successful reply passed')
        mode='normal';p,w,st=setup('foreign')
        foreign={**p,'tenantId':'tenant-b'}
        try:brain.install('qq',foreign)
        except ValueError:pass
        else:raise AssertionError('foreign tenant installed a policy')
        p2={**p,'botId':'other-bot'}
        assert not brain.valid('qq',{**w,'botId':'other-bot'})
        brain.observe('qq',NS(get_group_id=lambda:'',get_sender_id=lambda:'12345678'),True)
        assert not brain.valid('qq',w),'foreground input did not interrupt cycle'
        print('PIPELINE tenant / bot / foreground interruption fences passed')
    finally:
        runtime['close']();await asyncio.sleep(0)
        from astrbot.core.utils.metrics import Metric
        # Finish already scheduled uploads first: they may create the periodic
        # flusher after an earlier cancellation. Then stop that idle SDK owner.
        metrics=[task for task in asyncio.all_tasks() if task is not asyncio.current_task() and getattr(task.get_coro(),'__qualname__','').startswith('Metric.') and getattr(task.get_coro(),'__qualname__','')!='Metric._flush_periodically']
        if metrics:await asyncio.wait_for(asyncio.gather(*metrics),5)
        flushers=[task for task in asyncio.all_tasks() if getattr(task.get_coro(),'__qualname__','')=='Metric._flush_periodically']
        for task in flushers:task.cancel()
        if flushers:await asyncio.gather(*flushers,return_exceptions=True)
        await db_helper.engine.dispose()


asyncio.run(main())
