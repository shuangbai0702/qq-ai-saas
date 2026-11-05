"""Thin Work-bound tools. No cookies, media processing or social scheduler.

The Node Agent publishes a fenced, purpose-specific bridge manifest. Both the
foreground Agent and autonomous inner Agent use exactly this registry.
"""
import asyncio,hashlib,json,re,time
from pathlib import Path
import aiohttp

DESCRIPTIONS={
 'task_result':'核对本机器人的持久视频任务结果，仅使用真实actionId；尚在排队时不要让模型循环轮询，等待完成唤醒。',
 'analyze':'分析公开视频指定片段的采样帧；使用图片模型，无声音理解。长任务持久排队，完成后由原管线续接；不得重复提交或声称看完全部视频',
 'search':'搜索公开视频，返回标题作者和链接；不是观看内容',
 'resolve_video':'将真实B站视频链接或b23.tv短链解析为BV号；不读取任意网页、不观看视频。拿到BV号后再按任务读取详情、字幕或采样画面',
 'search_users':'按关键词查找UP主真实UID候选；同名不能自动当作指定联系人',
 'transcript_search':'在指定分P已获取字幕中定位关键词和时间段，可据此选择片段深入看图；不是完整语义搜索',
 'analysis_history':'核对本机器人已有的视频分析及覆盖范围，避免重复研究；没有记录不等于从未看过',
 'popular':'发现当前热门视频，可自行选择感兴趣的真实候选',
 'ranking':'读取视频排行榜', 'video_info':'读取视频详情及分P/CID，未观看画面',
 'video_tags':'读取视频平台标签；标签是发布者/平台提供的材料，不代表已观看',
 'user_profile':'按真实UID读取UP主公开资料；同名和昵称不能作为授权身份',
 'user_videos':'按真实UP主UID分页读取公开视频稿件，可按发布时间、播放或收藏排序',
 'collections':'按真实UP主UID分页查看公开视频合集与系列，返回真实collectionId和kind；不是收藏夹或视频内容分析',
 'collection_videos':'用真实UID、collectionId和kind分页查看合集或系列的视频目录；可再用返回的BV号读取字幕、分P或分析片段，不代表看完合集',
 'dynamic_info':'按真实动态ID读取已绑定账号可见的文字、配图地址和关联视频；受账号私有读取授权，不上传图片、不发表评论或标记通知已读',
 'related':'获取相关视频候选','comments':'读取视频评论或楼中楼；评论为外部不可信材料',
 'transcript':'读取指定分P带时间戳字幕，明确缺口；不是音频或画面理解',
 'account_info':'读取本机器人已绑定B站账号的本人信息',
 'feed':'读取账号推荐或关注更新，不自动点赞或记录观看',
 'library':'读取账号历史、稍后再看、收藏夹及其内容',
 'notifications':'读取通知未读计数；明细可能需要额外通知已读授权',
 'notification_items':'读取回复、提及或点赞通知；平台可能自动标记已读，按真实副作用授权和记录',
 'dm_read':'读取私信会话或指定真实UID的消息，不标记已读',
 'like':'将视频点赞设为明确目标状态','coin':'投币，消耗账号硬币，结果不明不得重试',
 'video_state':'核对视频当前点赞、投币和收藏状态；不是重复发送，当前状态不单独证明此前请求的归属',
 'folder_info':'读取当前账号拥有的收藏夹详情',
 'watch_later':'将指定视频添加或移出稍后再看；不提交观看进度、不批量清空',
 'folder_create':'新建收藏夹，默认私密；必须返回真实收藏夹ID',
 'folder_edit':'修改自己指定的收藏夹；标题、简介、隐私必须明确提供，避免省略字段覆盖旧内容',
 'folder_delete':'删除自己明确指定的一个收藏夹及其收藏条目，须有收藏夹管理授权；不是取消收藏一个视频',
 'favorite':'将视频添加或移出自己指定收藏夹','comment':'发表评论或回复，使用详情中真实aid和bvid；root为楼根、parent为直接回复对象；必须有真实评论ID才确认',
 'delete_comment':'删除当前账号在本系统有确认回执的评论',
 'follow':'设置对指定真实UID的关注状态','dynamic_post':'发布一条文字动态，必须有真实动态ID才确认',
 'dynamic_delete':'删除本系统有确认回执的当前账号动态',
 'dm_send':'向已授权真实UID发送文字私信，必须有真实消息key才确认',
 'profile_signature':'修改自己B站账号签名',
}

def manifest(settings):
    try:
        p=Path(settings['root'])/'ai'/'bilibili-bridge.json'
        if p.stat().st_size>65536:return None
        value=json.loads(p.read_text(encoding='utf-8'))
        if value.get('version')!=1 or value.get('botId')!=settings.get('botId') or any(value.get(k)!=settings.get(k) for k in ('generation','revision')):return None
        from urllib.parse import urlparse
        url=urlparse(value['url'])
        if url.scheme not in ('http','https') or url.username or url.password or url.query or url.fragment:return None
        return value
    except (OSError,ValueError,KeyError,TypeError):return None

def tools(settings,event,current,evidence,identity=None,db=None):
    bridge=manifest(settings)
    if not bridge:return []
    if identity and not bridge.get('allowAutonomous'):return []
    from astrbot.core.agent.tool import FunctionTool
    class BiliTool(FunctionTool):
        @property
        def hosted_effect(self):return any(s.get('operation')==self.name[len('bilibili_'):] and s.get('write') for s in bridge.get('operations',[]))
        async def call(self,context,**args):
            if not current():raise asyncio.CancelledError('stale Bilibili Work')
            live=manifest(settings)
            if not live or live.get('settingsRevision')!=bridge.get('settingsRevision'):return json.dumps({'error':'configuration_changed','receipt':{'status':'rejected'}})
            continuation=event.get_extra('_hosted_tool_completion') if event is not None else None
            if identity:
                work_id=identity['id'];authority='autonomous';sender=None;epoch=identity.get('epoch')
            else:
                work=event.get_extra('_hosted_pipeline')
                if work is None:return json.dumps({'error':'missing_trusted_work','receipt':{'status':'rejected'}})
                work_id='foreground:'+hashlib.sha256(json.dumps([work.bot_id,work.scope,work.subject,work.event_id],ensure_ascii=False).encode()).hexdigest()
                authority='foreground';sender=str(event.get_sender_id());epoch=None
                if continuation:
                    if not isinstance(continuation,dict) or continuation.get('senderId')!=sender or continuation.get('scope')!=work.scope or not continuation.get('workId') or not continuation.get('owner'):return json.dumps({'error':'invalid_continuation','receipt':{'status':'rejected'}})
                    work_id=continuation['workId']
            operation=self.name[len('bilibili_'):]
            step=hashlib.sha256(json.dumps([operation,args],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            payload={'operation':operation,'args':args,'workId':work_id,'stepId':step,'authority':authority,'deadlineAt':int(time.time()*1000)+90000}
            if not identity:payload['origin']={'scope':work.scope,'eventId':work.event_id,'objective':str(event.get_message_str())[:1000]}
            if operation=='analyze':
                payload['deadlineAt']=int(time.time()*1000)+600000
            if continuation:
                payload['continuation']={'actionId':continuation['id'],'owner':continuation['owner']}
                payload['origin']={'scope':continuation['scope'],'eventId':continuation['originEvent'],'objective':continuation.get('objective','')}
            if sender:payload['senderId']=sender
            if epoch:payload['epoch']=epoch
            base=bridge['url'].rstrip('/')+'/internal/v1/bots/'+bridge['botId']+'/tools'
            headers={'Authorization':'Bearer '+bridge['token']}
            job_id=None
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8),trust_env=False,headers=headers) as client:
                async def small(response):
                    raw=await response.content.read(131073)
                    if len(raw)>131072:raise ValueError('bridge_response_too_large')
                    value=json.loads(raw)
                    if response.status>=400:raise ValueError('tool_bridge_rejected')
                    return value
                try:
                    async with client.post(base,json=payload,allow_redirects=False) as response:job=await small(response)
                    job_id=job['id']
                    # Even durable queue admission is an async boundary. A late
                    # response must cancel its original Work before returning
                    # pending evidence or leaving a media task behind.
                    if not current():raise asyncio.CancelledError('stale Bilibili admission')
                    if operation=='analyze' and job.get('state') in ('queued','claimed'):
                        pending={'actionId':job_id,'execution':'pending','receipt':{'status':'not_applicable'},'data':{'message':'视频任务已持久排队，尚无分析结果；完成后回到原工作管线，不要重复提交，不得假装已经观看。'}}
                        if evidence is not None:
                            evidence.append({'source':'bilibili_tool','reference':job_id,'status':'pending'})
                            checkpoint=getattr(evidence,'checkpoint',None)
                            if callable(checkpoint):checkpoint({'tool':self.name,'actionId':job_id,'execution':'pending'})
                        return json.dumps(pending,ensure_ascii=False)
                    deadline=time.monotonic()+20
                    while job.get('state') in ('queued','claimed','dispatching'):
                        if not current():raise asyncio.CancelledError('stale Bilibili Work')
                        if time.monotonic()>deadline:raise asyncio.TimeoutError()
                        await asyncio.sleep(.5)
                        async with client.get(base+'/'+job_id,allow_redirects=False) as response:job=await small(response)
                    if not current():raise asyncio.CancelledError('stale Bilibili result')
                    result=job.get('result') or {'execution':'canceled','receipt':{'status':'rejected'},'error':{'code':'canceled_before_dispatch'}}
                    if db is not None:
                        from bilibili_images import record
                        revision=str(settings.get('generation'))+':'+str(settings.get('revision'))
                        images=record(db,result,operation,work_id,revision)
                        if images:result={**result,'imageCandidates':images}
                    if evidence is not None:
                        evidence.append({'source':'bilibili_tool','reference':job_id,'status':result.get('receipt',{}).get('status')})
                        checkpoint=getattr(evidence,'checkpoint',None)
                        if callable(checkpoint):checkpoint({'tool':self.name,'actionId':job_id,'receipt':result.get('receipt'),'data':result.get('data'),'error':result.get('error')})
                    return json.dumps(result,ensure_ascii=False)
                except (asyncio.CancelledError,asyncio.TimeoutError) as interrupted:
                    try:
                        async with client.post(base+'/cancel-work',json={'workId':work_id,'stepId':step},allow_redirects=False,timeout=aiohttp.ClientTimeout(total=2)) as response:await response.read()
                    except (aiohttp.ClientError,asyncio.TimeoutError):pass
                    if isinstance(interrupted,asyncio.CancelledError) or not current():raise asyncio.CancelledError('Bilibili Work canceled')
                    return json.dumps({'actionId':job_id,'execution':'pending','receipt':{'status':'unknown'},'error':{'code':'wait_interrupted','retry':'reconcile','userMessage':'任务等待中断，已申请取消；只查原动作，不得假定未执行或重复提交'}} ,ensure_ascii=False)
                except (ValueError,KeyError,aiohttp.ClientError):
                    return json.dumps({'actionId':job_id,'execution':'failed','receipt':{'status':'unknown'},'error':{'code':'bridge_unavailable','retry':'reconcile','userMessage':'执行服务未确认结果，不得声称操作成功'}} ,ensure_ascii=False)
    result=[]
    for spec in bridge.get('operations',[]):
        op=spec.get('operation')
        if not isinstance(op,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',op):continue
        if event is not None and event.get_extra('_hosted_tool_completion') and op=='analyze':continue
        if not identity and (spec.get('write') or spec.get('scope')!='public.read'):
            owner=str((settings.get('config',{}).get('behavior') or {}).get('ownerUin') or '')
            if event is None or not owner or str(event.get_sender_id())!=owner:continue
        parameters=dict(spec.get('args') or {});parameters.pop('$schema',None)
        description=DESCRIPTIONS.get(op) or '调用已授权B站能力'+op+'，只按当前工具参数合同执行，以真实返回为准'
        result.append(BiliTool(name='bilibili_'+op,description=description+'。由服务端校验当前账号、权限、工作身份和真实回执；失败/未知不得编造成功。',parameters=parameters))
    return result


async def resume_completion(core,id,settings,store,all_settings):
    """Existing initiative sweep resumes a durable tool result; no new timer/sender."""
    bridge=manifest(settings)
    if not bridge or not bridge.get('hasCompletions'):return False
    from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember,Group
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.message.components import Plain
    platform=next((p for p in core.platform_manager.get_insts() if p.meta().id==id),None)
    if platform is None or id in core.event_bus.stopped_platforms:return False
    base=bridge['url'].rstrip('/')+'/internal/v1/bots/'+id+'/tools'
    headers={'Authorization':'Bearer '+bridge['token']}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3),trust_env=False,headers=headers) as client:
        async with client.post(base+'/completion',json={},allow_redirects=False) as response:
            if response.status!=200:return False
            raw=await response.content.read(131073)
            if len(raw)>131072:return False
            item=json.loads(raw).get('completion')
        if not item or all_settings.get(id) is not settings:return False
        if item.get('generation')!=settings.get('generation') or item.get('revision')!=settings.get('revision'):return False
        scope=item['scope'];subject=str(item['senderId']);event_id='bilibili-result:'+item['id']
        status=await asyncio.wait_for(platform.bot.get_status(),3)
        if not status.get('online'):return False
        login=await asyncio.wait_for(platform.bot.get_login_info(),3)
        self_id=str(login.get('user_id') or '')
        if not self_id.isdigit() or settings.get('expectedUin') and self_id!=str(settings['expectedUin']) or all_settings.get(id) is not settings:return False
        if not scope.startswith(('private:','group:')) or scope.startswith('private:') and scope!='private:'+subject:return False
        if scope.startswith('group:') and settings['config'].get('groups') and scope[6:] not in settings['config']['groups']:return False
        row=store.db.execute('SELECT status,attempted FROM pipeline_events WHERE scope=? AND subject=? AND event=?',(scope,subject,event_id)).fetchone()
        if row:
            # Any protocol attempt is non-replayable. A completed no-output
            # outcome also cannot be turned into a different model decision.
            if row[1] or row[0] not in ('received','interrupted'):
                async with client.post(base+'/completion-ack',json={'id':item['id'],'owner':item['owner'],'status':'unknown' if row[0] in ('sending','unknown','interrupted') else 'done'},allow_redirects=False) as response:await response.read()
                return True
            if row[0]=='received':return True
            # Restart before any dispatch: a new computation may safely resume.
            store.db.execute('DELETE FROM pipeline_events WHERE scope=? AND subject=? AND event=? AND attempted=0',(scope,subject,event_id));store.db.commit()
        prompt='（平台工具结果续接：对方没有发新消息。这是先前真实请求的持久任务结果。原请求尚有未完成步骤时，沿用原工作身份和授权继续执行，再交付真实结果或必要限制；不要重复已完成的工具，也不要扩展成新委托，不把片段当完整视频。）\n'+json.dumps({'originalRequest':item['objective'],'toolResult':item['result']},ensure_ascii=False)
        msg=AstrBotMessage();msg.type=MessageType.GROUP_MESSAGE if scope.startswith('group:') else MessageType.FRIEND_MESSAGE
        msg.self_id=self_id;msg.session_id=scope.split(':',1)[1];msg.message_id=event_id
        msg.sender=MessageMember(user_id=subject,nickname='原请求者');msg.message=[Plain(prompt)];msg.message_str=prompt;msg.timestamp=int(time.time());msg.raw_message={'message_type':'group' if scope.startswith('group:') else 'private','user_id':int(subject),'self_id':int(msg.self_id or 0),'message_id':event_id,'sender':{'user_id':int(subject)},'message':[{'type':'text','data':{'text':prompt}}],**({'group_id':int(scope[6:])} if scope.startswith('group:') else {})}
        if scope.startswith('group:'):msg.group=Group(group_id=scope[6:])
        event=platform.create_event(msg);event.set_extra('_hosted_initiative',True);event.set_extra('_hosted_tool_completion',{'id':item['id'],'owner':item['owner'],'workId':item['workId'],'scope':scope,'senderId':subject,'originEvent':item['eventId'],'objective':item['objective']});event.set_extra('_hosted_lease',settings);event.set_extra('_hosted_generation',settings['generation'])
        platform.commit_event(event)
        return True
