"""Event-driven autonomous work inside the existing hosted runtime.

PostgreSQL/Node owns the ledger and wakes: an accepted foreground handoff
becomes a goal, its due wake dispatches one inner execution directly, and the
actual result settles the goal. This adapter owns only that bounded model call
and the current member's existing context, tools and pipeline. There is no
outer orientation/planning loop. Timed activity and model-selected rest use
the same durable scheduler; owner contact and group participation stay separate.
"""
import asyncio,hashlib,json,re,time
from types import SimpleNamespace
import autonomy,initiative,companion_state,bot_pipeline
from native_internal import RunControl,run,WorkStopped

ZERO_USAGE={'tokens':0,'modelCalls':0,'toolCalls':0}
class PreflightRejected(ValueError):pass


def evidence_limits(evidence):
    # The backend labels what the actual tool can establish. A model's
    # successful task summary cannot upgrade a source into verified knowledge.
    levels={
        'bilibili_public_search':'仅检索元数据；未读取原文或媒体，不能据此验证内容细节。',
        'existing_companion_state':'只证明读取了当前人物状态，不是外部事实核验。',
        'existing_memory':'既有记忆中的主张，未经本轮独立核验。',
        'qq_group_context':'已核对机器人和主人群身份，读取协议成员及本机器人近期收到的消息，不代表完整群历史或实时画面。',
        'self_interest_choice':'只证明根据本轮真实读取形成了自主关注选择，不代表已掌握主题知识或获得新权限。',
    }
    return list(dict.fromkeys(levels.get(item.get('source'),'证据源能力范围未经声明，不应推断已验证其内容。') for item in evidence))[:6]


def json_object(text):
    value=text.strip()
    if value.startswith('```'):
        value=re.sub(r'^```(?:json)?\s*|\s*```$','',value,flags=re.I)
    parsed=json.loads(value)
    if not isinstance(parsed,dict):raise ValueError('decision_not_object')
    return parsed


def bounded(value,depth=0):
    if depth>5:return None
    if isinstance(value,str):return value[:350]
    if isinstance(value,list):return [bounded(x,depth+1) for x in value[-4:]]
    if isinstance(value,dict):return {k:bounded(v,depth+1) for k,v in list(value.items())[:20]}
    return value


class AutonomousRuntime:
    def __init__(self,core,settings,store,provider,metrics,care,prepare_context):
        self.core=core;self.settings=settings;self.store=store;self.provider=provider
        self.metrics=metrics;self.care=care;self.prepare_context=prepare_context
        self.policies={};self.running={};self.events={};self.feedbacks={};self.pending_ambient={};self.interrupted={};self.tasks={};self.closed=False

    def owns(self,id):
        # The outer planning loop is gone, so autonomous ownership no longer
        # suppresses anything: the legacy initiative sweep, ambient group
        # participation and pending proactive deliveries run in every mode.
        # Due handoff work is owned by the PG ledger, not by this flag.
        return False

    def install(self,id,value):
        s=self.settings.get(id)
        if not s or not s.get('tenantId'):raise ValueError('member_identity_unavailable')
        for key in ('tenantId','botId','generation','revision'):
            if value.get(key)!=s.get(key):raise ValueError('member_identity_mismatch')
        if type(value.get('epoch')) is not int or value['epoch']<1:raise ValueError('invalid_epoch')
        wall=time.time()*1000
        if type(value.get('until')) not in (int,float) or not wall<value['until']<=wall+16000:raise ValueError('invalid_permit')
        cfg=value.get('config',{})
        if cfg.get('mode') not in ('passive','active') or cfg.get('rollout') not in ('shadow','live'):raise ValueError('invalid_autonomy_policy')
        old=self.policies.get(id)
        if old and value['epoch']<old['epoch']:raise ValueError('stale_autonomy_epoch')
        if old and value['epoch']==old['epoch'] and old['config']!=cfg:raise ValueError('policy_changed_without_epoch')
        if old and value['epoch']==old['epoch'] and value.get('issuedAt',0)<old.get('issuedAt',0):raise ValueError('stale_autonomy_permit')
        observed=value.get('observedThrough',0)
        if type(observed) not in (int,float) or not 0<=observed<=wall+60000:raise ValueError('invalid_observation_ack')
        if old and value['epoch']==old['epoch']:observed=max(observed,old.get('observedThrough',0))
        self.policies[id]={**value,'observedThrough':observed,'lease':s,'_permit_deadline':time.monotonic()+(value['until']-wall)/1000}
        experience=value.get('experience')
        if isinstance(experience,dict):self.store(id,s).autonomous_experience=json.loads(json.dumps(experience))
        feedback=value.get('latestFeedback')
        if isinstance(feedback,dict) and feedback.get('phase')=='foreground_result' and isinstance(feedback.get('result'),dict) and type(feedback.get('at')) in (int,float) and 0<feedback['at']<=wall+60000 and feedback['at']>self.feedbacks.get(id,{}).get('at',0):
            self.feedbacks[id]=json.loads(json.dumps(feedback))
        if old and (value['epoch']!=old['epoch'] or value.get('cycleId')!=old.get('cycleId')):self.cancel(id)
        observation=self.events.get(id)
        if observation and observation.get('phase')=='foreground_result' and observation['at']<=observed:
            pending=self.pending_ambient.pop(id,None)
            if pending and pending['at']>observed:self.events[id]=pending
        pending=bool(self.store(id,s).db.execute("SELECT 1 FROM pipeline_handoffs WHERE epoch=? AND state='pending' LIMIT 1",(value['epoch'],)).fetchone()) if cfg['mode']=='active' else False
        return {'epoch':value['epoch'],'mode':cfg['mode'],'rollout':cfg['rollout'],'pendingHandoffs':pending}

    def permit_current(self,p):
        return p['until']>time.time()*1000 and time.monotonic()<p['_permit_deadline']

    def validation_error(self,id,identity,effect=False):
        p=self.policies.get(id);s=self.settings.get(id)
        if self.closed:return 'runtime_closed'
        if not p or not s:return 'member_missing'
        if p['lease'] is not s:return 'member_replaced'
        if id in self.core.event_bus.stopped_platforms:return 'platform_stopped'
        if p['config']['mode']!='active':return 'mode_changed'
        if identity.get('_model_origin') and not identity.get('_continuation') and not s['config'].get('proactive'):return 'autonomy_disabled'
        if not self.permit_current(p):return 'permit_expired'
        if self.interrupted.get(id)==identity.get('cycleId'):return 'cycle_interrupted'
        for key in ('tenantId','botId','epoch','generation','revision','cycleId'):
            if identity.get(key)!=p.get(key):return 'identity_'+key
        if identity.get('principal')!='autonomous-service':return 'principal_invalid'
        if identity.get('expiresAt',0)<=time.time()*1000:return 'work_expired'
        if identity.get('feedbackVersion',0)!=self.feedbacks.get(id,{}).get('at',0):return 'feedback_changed'
        if effect and p['config']['rollout']!='live':return 'shadow_effect'
        if effect and p.get('actionId')!=identity.get('id'):return 'action_changed'
        return ''

    def valid(self,id,identity,effect=False):
        return not self.validation_error(id,identity,effect)

    def cancel(self,id,invalidate=False):
        control=self.running.get(id)
        if control:control.cancel.set()
        p=self.policies.get(id)
        if invalidate and p and p.get('cycleId'):self.interrupted[id]=p['cycleId']

    def effect_current(self,event):
        id=event.get_platform_id();identity=event.get_extra('_hosted_internal_work')
        if not self.valid(id,identity,effect=True):return False
        action=event.get_extra('_hosted_autonomous_action') or {}
        if not self.store(id,self.settings[id]).pipeline.handoff_current(action.get('goalId')):return False
        if (action.get('requestSource') or {}).get('origin')=='model' and not ((action.get('requestSource') or {}).get('parentActionId') and action.get('kind')=='message'):return False
        binding=(action.get('requestSource') or {}).get('agreement')
        if binding:
            import agreement_state
            if not agreement_state.task_current(self.store(id,self.settings[id]).db,binding):return False
        if action.get('kind')!='message':return True
        s=self.settings[id];st=self.store(id,s);scope=action.get('target','');now=time.time()
        if event.get_extra('_hosted_requested_reply'):
            request=st.pipeline.request_reply(identity,action.get('goalId'),action.get('requestSource'),identity['id'],current_lease=self.settings[id])
            return bool(request and request['scope']==scope)
        row=st.db.execute('SELECT last_user,opted_out FROM contacts WHERE scope=?',(scope,)).fetchone()
        return bool(row and not row[1] and row[0]==event.get_extra('_hosted_internal_last_user') and s['config'].get('proactive') and not initiative.quiet(s['config'],now) and not initiative.paused(st.db,scope,now))

    def release(self,id):
        self.policies.pop(id,None);self.cancel(id);self.events.pop(id,None);self.feedbacks.pop(id,None);self.pending_ambient.pop(id,None);self.interrupted.pop(id,None)

    async def drain(self,id):
        self.release(id);task=self.tasks.get(id)
        if task and task is not asyncio.current_task():
            try:await asyncio.wait_for(asyncio.shield(task),20)
            except (asyncio.TimeoutError,asyncio.CancelledError):
                task.cancel();await asyncio.gather(task,return_exceptions=True)

    def observe(self,id,event,directed):
        p=self.policies.get(id)
        if not p or p['config']['mode']!='active':return
        scope='group:'+str(event.get_group_id()) if event.get_group_id() else 'private:'+str(event.get_sender_id())
        if not directed and not autonomy.group_allowed(self.settings[id]['config'],scope):return
        # Ambient traffic follows the outer wake policy. The passive group's
        # inspection interval cannot delay or cancel autonomous work.
        sender=str(event.get_sender_id());owner=str((self.settings[id]['config'].get('behavior') or {}).get('ownerUin') or '')
        observation={'at':max(time.time()*1000,self.events.get(id,{}).get('at',0)+1),'scope':scope,'directed':bool(directed),'senderId':sender,'owner':bool(owner and sender==owner),'eventId':str(getattr(getattr(event,'message_obj',None),'message_id','')),'text':event.get_message_str()[:1000] if callable(getattr(event,'get_message_str',None)) else ''}
        previous=self.events.get(id)
        # Hold directed input through its actual foreground result and durable
        # PG acknowledgement; ambient traffic owns just one coalesced slot.
        if not directed and previous and previous['directed'] and (previous.get('phase')!='foreground_result' or previous['at']>p.get('observedThrough',0)):
            self.pending_ambient[id]=observation
            return
        self.events[id]=observation
        if directed:
            self.pending_ambient.pop(id,None)
            if p.get('cycleId'):self.interrupted[id]=p['cycleId']
            self.cancel(id)

    def completed(self,id,event,work):
        """Contribute actual foreground feedback through the existing observer."""
        observation=self.events.get(id);p=self.policies.get(id)
        if not observation or not p or p['config']['mode']!='active' or not work.observes_input or not work.ledger_owner:return
        if observation['eventId']!=work.event_id or observation['scope']!=work.scope or observation['senderId']!=work.subject:return
        # Returning from an ingress coroutine does not finish its Work when a
        # batch owner will execute it later. Keep directed input pending until
        # that owner commits the actual model/tool/delivery result.
        if work.status in ('waiting','combined'):return
        handoff=event.get_extra('_hosted_task_handoff')
        # Observing an ambient message is not an inner execution result. Only
        # actual model/tool work, a dispatch attempt or a registered handoff
        # may advance its feedback fence. Directed input retains its priority.
        executed=work.attempted>0 or any(step.get('stage')=='model_tools' for step in getattr(work,'trace',())) or bool(event.get_extra('_hosted_tool_results') or handoff)
        if not observation['directed'] and not executed:return
        observation.update(at=max(time.time()*1000,observation['at']+1),phase='foreground_result',result={'status':work.status,'confirmedMessages':work.confirmed_messages,'protocolAttempts':work.attempted,'protocolConfirmed':work.confirmed,'messageIds':list(event.get_extra('_hosted_protocol_receipts',[]))[:4],'evidence':list(event.get_extra('_hosted_research_evidence',[]))[:8],'toolResults':list(event.get_extra('_hosted_tool_results',[]))[-8:],'response':event.get_extra('_hosted_final_text','')[:600],'reason':work.reason,'taskHandoff':{k:handoff.get(k) for k in ('requestId','state','receipt')} if handoff else None})
        observation['at']=max(observation['at'],self.feedbacks.get(id,{}).get('at',0)+1)
        self.feedbacks[id]=json.loads(json.dumps(observation))

    def foreground_prompt(self,id,event):
        p=self.policies.get(id)
        if not p or p['config']['mode']!='active' or not event.get_extra('_hosted_handoff_available'):return ''
        return '\n[后台任务登记]\n主动模式可以把真实请求登记为平台后台任务，到期后由内层按登记内容执行。以下规则适用于本轮决定参与交流之后，不替代群聊的接话判断。先理解真实用户意图：普通闲聊、已有结果解释和连续对话自然回应；本轮即时任务由内层直接使用当前已授权工具完成，工具失败时根据真实结果调整做法。需要跨轮持续推进或延后提醒且你决定接受时，直接调用propose_background_task把完整要求和delay_seconds登记到平台任务账本，不能只记住或提出约定后就停止；若已登记用户的proposed约定，用其真实id/revision并设置accept_agreement=true，在同一事务接受并交接。指定钟点优先用run_at传带时区的ISO时间，程序换算延迟；相对等待使用delay_seconds。以本轮真实时间判断日期，不明确就询问；不要将已完成的即时任务再次登记执行。由模型判断意图，不依赖关键词。延续当前话题的普通聊天不重复建任务；需要变更或停止已登记任务时用cancel_background_task查看并取消原任务，不自造任务ID。只有真实工具回执才能证明登记；pending表示等待后台登记确认，不是保证准点执行。未成功登记时，明确说明尚未安排，不用聊天文字承诺稍后会自动回来。登记、接受请求和完成执行分别表述；没有对应工具结果及目的会话的发送回执，不能声称已在其他会话联系、发言或完成任务。当前私聊回复送达只证明本次回复送达。\n'

    def foreground_tools(self,id,event,current):
        p=self.policies.get(id)
        if not p or p['config']['mode']!='active':return []
        from astrbot.core.agent.tool import FunctionTool
        runtime=self;s=self.settings[id];st=self.store(id,s)
        event.set_extra('_hosted_handoff_available',True)
        class Propose(FunctionTool):
            async def call(self,context,objective,delay_seconds=0,kind='message',agreement_id='',agreement_revision=None,accept_agreement=False,run_at=''):
                if context.context.event is not event:return json.dumps({'queued':False,'reason':'tool_event_mismatch'})
                if event.get_extra('_hosted_tool_completion'):return json.dumps({'queued':False,'reason':'real_message_work_required'})
                live=runtime.policies.get(id)
                if not current() or not live or live['lease'] is not s or live['epoch']!=p['epoch'] or live['config']['mode']!='active' or live['until']<=time.time()*1000:raise asyncio.CancelledError('stale task handoff')
                work=event.get_extra('_hosted_pipeline')
                if kind=='research' and getattr(work,'source',None) in ('scheduled_contact','owner_contact') and not s['config'].get('proactive'):return json.dumps({'queued':False,'reason':'autonomy_disabled'})
                if not isinstance(objective,str) or not 1<=len(objective.strip())<=1000 or type(delay_seconds) is not int or not 0<=delay_seconds<=604800:return json.dumps({'queued':False,'reason':'invalid_task_proposal'})
                identity={k:live[k] for k in ('tenantId','botId','generation','revision','epoch')}
                if kind not in ('message','research'):return json.dumps({'queued':False,'reason':'invalid_task_kind'})
                if type(accept_agreement) is not bool:return json.dumps({'queued':False,'reason':'invalid_agreement_acceptance'})
                request={'objective':objective.strip(),'delaySeconds':delay_seconds,'kind':kind}
                if run_at:
                    # The model interprets the requested time; code validates an
                    # explicit timezone and computes the delay, never keywords.
                    from datetime import datetime
                    try:
                        moment=datetime.fromisoformat(run_at.replace('Z','+00:00'))
                        if moment.tzinfo is None:raise ValueError('timezone_required')
                        when=int(moment.timestamp()*1000)
                        if delay_seconds or not int(time.time()*1000)<=when<=int(time.time()*1000)+604800000:raise ValueError('time_range')
                    except (ValueError,TypeError,AttributeError,OverflowError):return json.dumps({'queued':False,'reason':'invalid_run_at'})
                    request['runAt']=when
                if accept_agreement:request['acceptAgreement']=True
                if agreement_id:
                    if not isinstance(agreement_id,str) or type(agreement_revision) is not int:return json.dumps({'queued':False,'reason':'agreement_revision_required'})
                    request['agreement']={'id':agreement_id,'revision':agreement_revision}
                try:result=st.pipeline.propose_handoff(work,identity,request)
                except ValueError as error:return json.dumps({'queued':False,'reason':str(error)})
                if accept_agreement:
                    import agreement_state
                    event.set_extra('_hosted_agreement_fence',(work.scope,agreement_state.agreements_stamp(st.db,work.scope)))
                event.set_extra('_hosted_task_handoff',result)
                return json.dumps({**result,'queued':result['state'] in ('pending','accepted'),'meaning':'真实请求已保存到平台任务交接账本，等待平台接受并安排到期执行；pending不等于已经完成平台登记或安排执行。任务、后续联系仍须通过权限和预算校验，不能承诺已执行或必定准点。'},ensure_ascii=False)
        result=[Propose(name='propose_background_task',description='登记跨轮或延后执行的任务。即时任务直接完成，不重复登记已完成动作。message用于原会话到期回复或提醒；research用于内部工具任务，可查询、研究或执行当前获授权能力；最终内部文字不会自动发QQ，工具仍可能产生真实外部写入。可在现有主动时刻根据真实兴趣登记，不冒充用户委托。delay_seconds是最早执行延迟。关联约定填写agreement_id及agreement_revision；若是当前真实用户提出、你决定接受的proposed事项，显式设置accept_agreement=true，以同一事务确认并登记任务；取消或修改约定会阻止旧版本任务。pending仅表示待平台确认，不是已经安排或履行。每条入站最多一项，同项可查询重试。',parameters={'type':'object','properties':{'objective':{'type':'string','minLength':1,'maxLength':1000},'delay_seconds':{'type':'integer','minimum':0,'maximum':604800},'run_at':{'type':'string','description':'明确钟点使用带时区的ISO时间，例如2026-11-08T15:15:00+08:00；与非零delay_seconds互斥，最多未来7天。'},'kind':{'type':'string','enum':['message','research']},'agreement_id':{'type':'string','maxLength':24},'agreement_revision':{'type':'integer','minimum':1},'accept_agreement':{'type':'boolean'}},'required':['objective'],'additionalProperties':False})]
        class Manage(FunctionTool):
            async def call(self,context,request_id=''):
                if event.get_extra('_hosted_tool_completion'):return json.dumps({'status':'rejected','code':'real_message_work_required'})
                live=runtime.policies.get(id)
                if context.context.event is not event or not current() or not live or live['lease'] is not s or live['epoch']!=p['epoch'] or live['config']['mode']!='active' or live['until']<=time.time()*1000:raise asyncio.CancelledError('stale task management')
                work=event.get_extra('_hosted_pipeline')
                if not request_id:return json.dumps({'tasks':st.pipeline.handoff_catalog(work)},ensure_ascii=False)
                try:value=st.pipeline.cancel_handoff(work,request_id)
                except ValueError:return json.dumps({'status':'rejected','code':'task_not_owned_or_not_available'})
                return json.dumps(value,ensure_ascii=False)
        result.append(Manage(name='cancel_background_task',description='查看或取消当前真实用户在当前会话登记的后台任务。省略request_id只列出本人任务；理解用户意图后，用列表真实ID取消。取消仅阻止后续执行，不能撤销已发生或结果未知的操作，不声称外部操作已撤销。修改任务先取消旧项，再按本轮真实要求登记新项。',parameters={'type':'object','properties':{'request_id':{'type':'string','maxLength':36}},'additionalProperties':False}))
        # Durable delegation supplements the same inner Agent; it does not
        # replace immediate execution or grant any tool permissions.
        import autonomous_goals
        result+=autonomous_goals.tools(s,event,st.db,current)
        return result

    def candidates(self,id,include_waiting=False):
        s=self.settings[id];p=self.policies[id];st=self.store(id,s);cfg=s['config'];b=cfg.get('behavior') or {};now=time.time()
        if not b.get('enabled',True) or not cfg.get('proactive') or initiative.quiet(cfg,now) or not autonomy.has_capacity(st.db,cfg,now):return []
        day=initiative.datetime.fromtimestamp(now,initiative.ZONE).date().isoformat()
        if st.db.execute('SELECT coalesce(sum(count),0) FROM deliveries WHERE day=?',(day,)).fetchone()[0]>=b.get('dailyProactiveLimit',10):return []
        owner=str(b.get('ownerUin') or '');result=[]
        rows=st.db.execute('SELECT scope,subject,self_id,last_user,last_bot,last_attempt,unanswered,opted_out,last_text,last_output FROM contacts ORDER BY last_user DESC LIMIT 50').fetchall()
        for scope,subject,self_id,user,bot,attempt,unanswered,opted,text,output in rows:
            if opted or unanswered>=b.get('maxUnanswered',4) or initiative.paused(st.db,scope,now):continue
            not_before=0
            if scope.startswith('private:'):
                if scope[8:]!=subject or not re.fullmatch(r'\d{5,20}',subject) or subject==self_id:continue
                if subject==owner:
                    if not b.get('ownerContactEnabled',autonomy.BEHAVIOR_DEFAULTS['ownerContactEnabled']):continue
                    row=st.db.execute('SELECT interaction FROM autonomy_owner WHERE owner=?',(owner,)).fetchone()
                    interaction=max(user,bot,row[0] if row else 0)
                    not_before=max(max(interaction,attempt)+b.get('ownerContactIntervalMinutes',30)*60,interaction+b.get('ownerContactIdleMinutes',10)*60)
                elif scope not in b.get('proactiveTargets',[]):continue
                else:not_before=max(user,bot,attempt)+b.get('proactiveIntervalSeconds',3600)
            else:
                if not autonomy.group_allowed(cfg,scope) or autonomy.group_muted(st.db,scope,now):continue
                # Passive participation's inspection clock is not an outer
                # message permission. The outer model chooses when to act.
            if not_before>now and not include_waiting:continue
            candidate={'scope':scope,'subject':subject,'selfId':self_id,'owner':bool(owner and scope=='private:'+owner),'lastUserAt':user,'lastReplyAt':bot,'lastAttemptAt':attempt,'unanswered':unanswered,'lastText':text,'lastOutput':output,'mayInitiate':True}
            if include_waiting:candidate.update(eligibleNow=not_before<=now,notBefore=int(not_before*1000))
            result.append(candidate)
        return result[:20]

    def observed_conversations(self,id):
        """Bounded existing facts, independent of permission to initiate contact."""
        s=self.settings[id];st=self.store(id,s);owner=str((s['config'].get('behavior') or {}).get('ownerUin') or '')
        event=self.events.get(id) or {};scopes=[]
        if owner:scopes.append('private:'+owner)
        if event.get('scope') and event['scope'] not in scopes:scopes.append(event['scope'])
        result=[]
        for scope in scopes[:2]:
            row=st.db.execute('SELECT subject,last_user,last_bot,last_text,last_output FROM contacts WHERE scope=?',(scope,)).fetchone()
            if not row:continue
            subject,user,bot,text,output=row
            receipt=st.db.execute("SELECT event,at,status,attempted,confirmed FROM pipeline_events WHERE scope=? AND subject=? AND source='message' ORDER BY at DESC LIMIT 1",(scope,subject)).fetchone()
            # A previous reply must not be presented as the result of a newer
            # message that is still in flight. The ledger has no message body;
            # the existing contact row supplies only its last bounded summary.
            outcome=None
            if receipt and abs(receipt[1]-user)<1:
                outcome={'eventId':receipt[0],'status':receipt[2],'protocolAttempts':receipt[3],'protocolConfirmed':receipt[4]}
            result.append({'scope':scope,'senderId':subject,'owner':bool(owner and subject==owner),'lastUserAt':int(user*1000),'lastInput':text[:180],'lastReplyAt':int(bot*1000) if bot else None,'lastReply':output[:400] if bot>=user else None,'outcome':outcome})
        return result

    def snapshot(self,id,ledger_only=False):
        s=self.settings[id];st=self.store(id,s);now=time.time()
        if ledger_only:
            import agreement_state
            return {'pendingHandoffs':st.pipeline.pending_handoffs(self.policies[id]['epoch']),
                    'agreementStates':agreement_state.task_states(st.db),'canceledHandoffs':st.pipeline.canceled_handoffs(),
                    'foregroundFeedback':self.feedbacks.get(id),'event':self.events.get(id),
                    'feedbackVersion':self.feedbacks.get(id,{}).get('at',0),
                    'foregroundBusy':st.active>(1 if id in self.running else 0) or bool(getattr(st,'waiting',0)),
                    'nextEventCheckSeconds':self.policies[id]['config'].get('minSleepSeconds',600)}
        candidates=self.candidates(id)
        # The supervisor sees private context, but the eventual task receives
        # only its destination-scoped context. Group output never inherits a
        # different person's private facts as a second injected memory.
        contexts=[{'scope':c['scope'],'dialogue':bounded(st.participation.context(c['scope'],cfg=s['config'].get('behavior') or {})),'companion':bounded(companion_state.context(st.db,s['config'],c['scope'],now))} for c in candidates[:3]]
        policy=self.policies[id]['config'];interval=policy.get('minSleepSeconds',600)
        event=self.events.get(id);delay=interval
        deferred=[{k:c[k] for k in ('scope','owner','notBefore')} for c in self.candidates(id,include_waiting=True) if not c['eligibleNow']][:3]
        # Self experiences come from the authoritative action ledger (source-
        # attributed tool tasks), never from persona facts: research results
        # are task outcomes, not the bot's personal history.
        experiences=bot_pipeline.experience_context(st,'self',s['config'].get('behavior') or {}).get('actions',[]) if (s['config'].get('behavior') or {}).get('memory',True) else []
        catalog=self.capability_catalog(id,candidates)
        available=[t['name'] for t in catalog['innerTools'] if t['available']]
        unavailable=['web_search']+[t['name'] for t in catalog['innerTools'] if not t['available']]
        behavior=s['config'].get('behavior') or {};start,end=behavior.get('quietStart',autonomy.BEHAVIOR_DEFAULTS['quietStart']),behavior.get('quietEnd',autonomy.BEHAVIOR_DEFAULTS['quietEnd'])
        quiet_hours={'startHour':start,'endHour':end,'enabled':start!=end,'activeNow':initiative.quiet(s['config'],now),'timezone':str(initiative.ZONE),'scope':'unsolicited_outbound_messages','meaning':'真实配置的主动消息安静时段；不表示联系人正在休息，普通入站回复仍按原管线处理。'}
        return {'foregroundBusy':st.active>(1 if id in self.running else 0),'feedbackVersion':self.feedbacks.get(id,{}).get('at',0),'foregroundFeedback':self.feedbacks.get(id), 'now':int(now*1000),'persona':s['config'].get('systemPrompt',''),'self':companion_state.context(st.db,s['config'],'self',now),'selfExperiences':experiences,'identity':{'ownerId':str((s['config'].get('behavior') or {}).get('ownerUin') or ''),'selfId':str(s.get('expectedUin') or '')},'contacts':candidates,'deferredContacts':deferred,'contexts':contexts,'observedConversations':self.observed_conversations(id),'event':event,'nextEventCheckSeconds':delay,'wakePolicy':{'idleCheckSeconds':interval,'maxSleepSeconds':policy.get('maxSleepSeconds',86400),'quietHours':quiet_hours},'availableTools':available,'unavailableTools':unavailable,**catalog,'pendingHandoffs':st.pipeline.pending_handoffs(self.policies[id]['epoch'])}

    def can_reflect(self,id):
        p=self.policies.get(id);s=self.settings.get(id)
        return bool(s and p and p['config'].get('rollout')=='live' and companion_state.enabled(s['config']) and (s['config'].get('behavior') or {}).get('memory',True))

    def control(self,id,identity):
        if id in self.running:raise PreflightRejected('background_work_busy')
        if not self.valid(id,identity):raise PreflightRejected('stale_internal_work')
        b=identity['budget']
        if any(type(b.get(k)) not in (int,float) or b[k]<=0 for k in ('modelCalls','tokens','seconds')) or type(b.get('toolCalls')) is not int or b['toolCalls']<0:raise ValueError('invalid_work_budget')
        if b['modelCalls']>64 or b['toolCalls']>128 or b['tokens']>2000000 or b['seconds']>180:raise ValueError('work_budget_exceeded')
        return RunControl(int(b['modelCalls']),int(b['toolCalls']),int(b['tokens']),b['seconds'],lambda:self.valid(id,identity))

    async def invoke(self,id,identity,prompt,system,tools=(),context=None,contexts=None,cache_phase=None,cache_namespace=None):
        # Service activations use the same admission boundary as real chats.
        await self.store(id,self.settings[id]).wait_compression()
        control=self.control(id,identity);s=self.settings[id];st=self.store(id,s)
        control.trace={'botId':s.get('botId',id),'cycleId':identity.get('cycleId'),'workId':identity.get('id'),'phase':cache_phase or 'inner_execution','systemSha':hashlib.sha256(system.encode()).hexdigest()[:16]}
        control.diagnostic_lease={key:s.get(key) for key in ('root','tenantId','botId','generation','revision')}
        if st.active or getattr(st,'waiting',0) or self.provider(id) is None:return {'status':'blocked','reason':'foreground_work_busy' if st.active or getattr(st,'waiting',0) else 'model_unavailable','text':'','usage':{'tokens':0,'modelCalls':0,'toolCalls':0,'estimated':False},'seconds':0,'evidence':[]}
        self.running[id]=control;st.active+=1;self.care.stamp(s['root'])
        from astrbot.core.platform.astr_message_event import current_message_event
        event=getattr(context,'event',None);token=current_message_event.set(event)
        epoch=event.get_extra('_hosted_memory_epoch',st.reflection.epoch()) if event else st.reflection.epoch()
        binding=((event.get_extra('_hosted_autonomous_action') or {}).get('requestSource') or {}).get('agreement') if event else None
        def current_work():
            import agreement_state
            return self.valid(id,identity) and st.reflection.epoch()==epoch and st.pipeline.handoff_current((event.get_extra('_hosted_autonomous_action') or {}).get('goalId') if event else None) and agreement_state.task_current(st.db,binding)
        control.current=current_work
        from model_request_budget import bind
        control.model_budget=bind(s,identity,current_work)
        control.output_tokens=8192 if tools else 4096
        token_limit=autonomy.decision_token_limit.set(control.output_tokens)
        try:
            from model_cache import decision_scope
            provider=self.provider(id)
            with decision_scope(provider,s,cache_namespace or cache_phase,identity.get('id'),cache_phase):
                result=await run(provider,control,prompt,system,tools=tools,context=context,contexts=contexts)
            self.metrics.setdefault(id,{})['lastAutonomousRun']={'status':result['status'],'usage':result['usage'],'seconds':result['seconds'],'at':time.time()}
            return result
        finally:
            current_message_event.reset(token)
            autonomy.decision_token_limit.reset(token_limit)
            # Internal continuity belongs to this Work only. Do not retain its
            # transcript across other tasks or keep media/tool data resident.
            if event and event.get_extra('_hosted_internal_work') and not event.get_extra('_hosted_main_activation'):
                st.model_journals.pop(('autonomous',identity['id']),None)
            if self.running.get(id) is control:self.running.pop(id,None)
            st.active-=1;st.slot_changed.set();self.care.stamp(s['root'])

    def tools(self,id,identity,scope):
        from astrbot.core.agent.tool import FunctionTool
        runtime=self;s=self.settings[id];st=self.store(id,s)
        class Recall(FunctionTool):
            async def call(self,context,query):
                if not runtime.valid(id,identity):raise WorkStopped('stale_internal_work')
                if not isinstance(query,str) or not 1<=len(query)<=160:raise ValueError('invalid_query')
                subject=scope[8:] if scope.startswith('private:') else '0' if scope=='self' else ''
                enabled=(s['config'].get('behavior') or {}).get('memory',True)
                facts=st.memory.recall(scope,subject,query,time.time(),limit=5,budget=1000) if enabled else []
                summaries=st.reflection.recall(scope,query,limit=2,subject=subject) if enabled else []
                runtime.running[id].evidence.append({'source':'existing_memory','reference':scope})
                return json.dumps({'facts':facts,'summaries':summaries,'meaning':'已有记忆材料，不是网络检索或新的经历'},ensure_ascii=False)
        tools=[companion_state.inspect_tool(st.db,s['config'],scope,lambda:runtime.valid(id,identity),on_read=lambda receipt:runtime.running[id].evidence.append({'source':receipt['source'],'reference':scope+':'+str(receipt['at'])})),Recall(name='recall_memory',description='在当前任务会话范围内查询现有长期记忆和摘要；非网络搜索',parameters={'type':'object','properties':{'query':{'type':'string','maxLength':160}},'required':['query'],'additionalProperties':False})]
        if self.can_reflect(id):
            memory_epoch=st.reflection.epoch()
            class ReflectInterest(FunctionTool):
                async def call(self,context,topic,operation,reason):
                    if not runtime.valid(id,identity) or not runtime.can_reflect(id) or st.reflection.epoch()!=memory_epoch:raise WorkStopped('self_reflection_stale')
                    control=runtime.running.get(id)
                    if not control:raise WorkStopped('self_reflection_has_no_work')
                    control.check()
                    result=companion_state.reflect_interest(st.db,topic,operation,reason,identity['id'],list(control.evidence),time.time())
                    control.evidence.append({'source':'self_interest_choice','reference':identity['id']+':'+result['topic']})
                    return json.dumps(result,ensure_ascii=False)
            tools.append(ReflectInterest(name='reflect_interest',description='在本轮真实读取后，自主增加、降低或放下自己的关注主题；写入原有自身兴趣，最多3个主题，不改人格、关系、权限或他人的记忆。explore增加关注，cool_down降低，drop放下；不是知识验证。',parameters={'type':'object','properties':{'topic':{'type':'string','minLength':2,'maxLength':50},'operation':{'type':'string','enum':['explore','cool_down','drop']},'reason':{'type':'string','minLength':1,'maxLength':240}},'required':['topic','operation','reason'],'additionalProperties':False}))
        from tool_outcome import structured
        return [structured(tool) for tool in tools]

    def task_tools(self,id,identity,scope,event=None):
        """Actual execution and metadata use the same shared feature registry."""
        result=self.tools(id,identity,scope)
        if event is None:return result
        import inner_tools
        runtime=self;s=self.settings[id];st=self.store(id,s);epoch=st.reflection.epoch()
        class Evidence:
            def append(self,value):runtime.running[id].evidence.append(value)
            def checkpoint(self,value):
                control=runtime.running[id];control.checkpoints.append(value);control.checkpoints[:]=control.checkpoints[-4:]
        binding=((event.get_extra('_hosted_autonomous_action') or {}).get('requestSource') or {}).get('agreement')
        def current_task():
            import agreement_state
            return self.valid(id,identity) and st.pipeline.handoff_current((event.get_extra('_hosted_autonomous_action') or {}).get('goalId')) and agreement_state.task_current(st.db,binding)
        result+=inner_tools.features(s,event,st.db,scope,current_task,identity=identity,evidence=Evidence(),memory_current=lambda:current_task() and st.reflection.epoch()==epoch,effect_current=lambda:current_task() and self.effect_current(event))
        import autonomous_goals
        result+=autonomous_goals.tools(s,event,st.db,current_task)
        if ((event.get_extra('_hosted_autonomous_action') or {}).get('requestSource') or {}).get('origin')=='model':
            result=[tool for tool in result if not getattr(tool,'hosted_effect',False)]
        result+=self.continuation_tools(id,identity,event,current_task)
        return result

    def continuation_tools(self,id,identity,event,current):
        """Only the current authenticated action may register its next step."""
        from astrbot.core.agent.tool import FunctionTool
        runtime=self;s=self.settings[id];st=self.store(id,s)
        def check(context):
            if context.context.event is not event or not current():raise asyncio.CancelledError('stale internal continuation')
        class Continue(FunctionTool):
            async def call(self,context,objective,delay_seconds=300,kind='research',target=''):
                check(context)
                if not isinstance(objective,str) or not 1<=len(objective.strip())<=1000 or type(delay_seconds) is not int or not 300<=delay_seconds<=604800:return json.dumps({'status':'rejected','reason':'invalid_continuation'})
                binding={k:identity[k] for k in ('tenantId','botId','generation','revision','epoch')}
                if kind not in ('research','message') or kind=='research' and target:return json.dumps({'status':'rejected','reason':'invalid_continuation_kind'})
                if kind=='message' and target not in {c['scope'] for c in runtime.candidates(id)}:return json.dumps({'status':'rejected','reason':'contact_not_currently_allowed'})
                try:value=st.pipeline.propose_handoff(event.get_extra('_hosted_pipeline'),binding,{'kind':kind,'scope':target or 'self','objective':objective.strip(),'delaySeconds':delay_seconds,'parentActionId':identity['id']})
                except ValueError as error:return json.dumps({'status':'rejected','reason':str(error)})
                return json.dumps({**value,'meaning':'后续步骤已持久化交接，pending仍须平台接受；不证明已执行或完成。'},ensure_ascii=False)
        class Rest(FunctionTool):
            async def call(self,context,seconds):
                check(context)
                maximum=runtime.policies[id]['config']['maxSleepSeconds']
                if type(seconds) is not int or not 300<=seconds<=maximum:return json.dumps({'status':'rejected','reason':'rest_out_of_range','minimum':300,'maximum':maximum})
                event.set_extra('_hosted_activity_rest',seconds)
                return json.dumps({'status':'accepted','nextActivityAfterSeconds':seconds,'meaning':'仅调整下一次通用自主活动；已有任务及真实入站不受影响。'},ensure_ascii=False)
        class Manage(FunctionTool):
            async def call(self,context,request_id=''):
                check(context);work=event.get_extra('_hosted_pipeline')
                if not request_id:return json.dumps({'tasks':st.pipeline.handoff_catalog(work)},ensure_ascii=False)
                try:return json.dumps(st.pipeline.cancel_handoff(work,request_id),ensure_ascii=False)
                except ValueError:return json.dumps({'status':'rejected','reason':'task_not_owned_or_not_available'})
        tools=[Continue(name='continue_background_task',description='根据当前目标的动机、真实成果和阻塞，自主决定是否登记一个可继续或修订的后续步骤，至少5分钟后执行。research用于当前授权内的内部工具任务，最终文字不自动发QQ；message仅选当前允许联系人。每个父动作最多一个，同项重试不重复创建。pending仍须平台确认。可以结束或放下目标；无需继续时不调用，不把放下伪称完成。',parameters={'type':'object','properties':{'objective':{'type':'string','minLength':1,'maxLength':1000},'delay_seconds':{'type':'integer','minimum':300,'maximum':604800},'kind':{'type':'string','enum':['research','message']},'target':{'type':'string','description':'message须选当前allowedContacts完整scope；research留空'}},'required':['objective'],'additionalProperties':False}),Manage(name='manage_autonomous_tasks',description='查看或取消本机器人服务身份创建的后续目标，不能管理人类委托。省略request_id查看；提供真实ID取消。修改先取消旧目标再创建后续步骤，已发生和未知动作不能撤销。',parameters={'type':'object','properties':{'request_id':{'type':'string','maxLength':36}},'additionalProperties':False})]
        if (event.get_extra('_hosted_autonomous_action') or {}).get('autonomousMoment'):
            tools.append(Rest(name='rest_until_next_activity',description='自主决定休息及下一次通用活动间隔。不会取消已有目标，不阻止正常聊天或到期任务。至少300秒，最多当前平台maxSleepSeconds。',parameters={'type':'object','properties':{'seconds':{'type':'integer','minimum':300,'maximum':self.policies[id]['config']['maxSleepSeconds']}},'required':['seconds'],'additionalProperties':False}))
        return tools

    def capability_catalog(self,id,contacts):
        from types import SimpleNamespace
        from research_tools import availability
        policy=self.policies[id]['config'];b=self.settings[id]['config'].get('behavior') or {}
        entries=[];descriptions={}
        from bilibili_tools import manifest as bilibili_manifest
        bili_bridge=bilibili_manifest(self.settings[id])
        search_enabled=b.get('bilibiliSearchEnabled',False) if bili_bridge is None else any(t.get('operation')=='search' for t in bili_bridge.get('operations',[])) and bili_bridge.get('allowAutonomous',False)
        if not search_enabled:descriptions['bilibili_search']={'name':'bilibili_search','available':False,'reason':'机器人未开启B站公开搜索；主动模式不授予此能力','blockedScope':'tool','code':'feature_disabled'}
        specs=[('research','内部工具任务：按目标查询、研究或执行已授权能力；最终文字不自动发聊天，工具可有真实写入，具体以innerTools和当前权限为准','research'),('message','自己选择本轮允许的对象和话题，交内层聊天并经公共管线发送',None),('profile_signature','按人物状态和意愿更新自己的QQ签名','profile_signature'),('qzone_publish','按自身经历、兴趣和意愿发布自己QQ空间的文字动态','qzone_publish')]
        for kind,description,cap in specs:
            # The catalogue describes inner configuration, never outer grants.
            authorized={'research':True,'message':True,'profile_signature':bool(b.get('qqProfile') and b.get('autoSignature',True)),'qzone_publish':bool(b.get('qzone'))}[kind]
            scope=contacts[0]['scope'] if kind=='message' and contacts else 'self'
            # Metadata-only handles are never executed and contain no Work
            # permit. QQ tool authorization is the same allowed() used at run.
            identity={'id':'capability-catalog','kind':kind,'principal':'autonomous-service'}
            extras={'_hosted_internal_work':identity,'_hosted_autonomous_action':{'kind':kind}}
            event=SimpleNamespace(get_extra=lambda key,default=None:extras.get(key,default))
            tools=self.task_tools(id,identity,scope,event) if authorized else []
            reasons=[];names=[]
            if not authorized:reasons.append('内层对应功能未开启')
            if policy.get('rollout')=='shadow' and kind!='research':reasons.append('影子模式只记录意图，不执行对外操作')
            for tool in tools:
                name=tool.name;ready=True;reason=''
                search_state=availability() if name=='bilibili_search' and bili_bridge is None else None
                if name.startswith('bilibili_') and bili_bridge and bili_bridge.get('cooldownUntil'):
                    from datetime import datetime,timezone
                    try:
                        remaining=(datetime.fromisoformat(bili_bridge['cooldownUntil'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()
                        if remaining>0:search_state={'available':False,'retryAfterSeconds':int(remaining)+1}
                    except (ValueError,TypeError):pass
                if search_state and not search_state['available']:ready=False;reason='B站公开搜索暂处服务冷却，执行时再次核对'
                if name=='recall_memory' and not b.get('memory',True):ready=False;reason='记忆已关闭，无可检索的记忆数据'
                effect=bool(getattr(tool,'hosted_effect',False))
                if effect and policy.get('rollout')=='shadow':ready=False;reason='影子模式不允许对外操作，读取能力仍可使用'
                descriptions[name]={'name':name,'description':tool.description,'parameters':tool.parameters,'sideEffect':effect,'enabled':True,'authorized':authorized,'available':ready,'readiness':'available_to_attempt' if ready else 'temporarily_unavailable','evidence':'not_executed_by_catalog',**({'reason':reason} if reason else {})}
                if search_state and not search_state['available']:descriptions[name].update(blockedScope='tool',code='service_cooldown',retryAfterSeconds=search_state['retryAfterSeconds'])
                names.append(name)
            if kind=='message' and not contacts:reasons.append('本轮没有可联系会话；可等待事件或选择其他能力')
            expected={'profile_signature':'hosted_qq_signature','qzone_publish':'hosted_qq_publish'}.get(kind)
            if authorized and expected and expected not in names:reasons.append('对应QQ功能开关或自动操作授权未开启')
            limits={}
            if kind=='message':limits={'targets':[c['scope'] for c in contacts],'delivery':'内层回复经OutputPlan投递，是否成功以协议回执为准'}
            if kind=='profile_signature':limits={'maxChars':40,'dailyLimit':b.get('dailyAutoSignatureLimit',2),'remaining':'执行前查现有操作账本；不得假设仍有额度'}
            if kind=='qzone_publish':limits={'maxChars':500,'visibility':b.get('qzoneVisibility','friends'),'protocolSupport':'尚未由本次调用验证；以实际QQ接口回执为准'}
            if kind=='research':limits={'network':'以innerTools中的实际B站工具、权限和回执为准；字幕不等于观看声画，未列出的能力不可用' if any(n.startswith('bilibili_') for n in names) else '联网工具未开启；仅现有状态与已开启的记忆读取'}
            entries.append({'kind':kind,'description':description,'authorized':authorized,'available':authorized and not reasons,'tools':names,'blockedReasons':reasons,'limits':limits})
        catalog=list(descriptions.values())
        revision=hashlib.sha256(json.dumps(catalog,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        return {'capabilityCatalog':entries,'innerTools':catalog,'capabilityRevision':revision}

    def make_event(self,id,identity,scope,objective,self_id):
        from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.message.components import Plain
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        platform=next(p for p in self.core.platform_manager.get_insts() if p.meta().id==id)
        msg=AstrBotMessage();msg.self_id=self_id;msg.message_id=identity['id'];msg.sender=MessageMember(user_id='0',nickname='自主服务任务')
        msg.message=[Plain(objective)];msg.message_str=objective;msg.raw_message=None
        msg.type=MessageType.GROUP_MESSAGE if scope.startswith('group:') else MessageType.FRIEND_MESSAGE
        msg.group_id=scope[6:] if scope.startswith('group:') else ''
        event=AiocqhttpMessageEvent(objective,msg,platform.meta(),scope.split(':',1)[1],platform.bot)
        s=self.settings[id];event.set_extra('_hosted_lease',s);event.set_extra('_hosted_generation',s['generation'])
        event.set_extra('_hosted_initiative',True);event.set_extra('_hosted_internal_work',identity)
        return event

    async def execute(self,id,data):
        identity=data['work'];action=data['action'];s=self.settings[id];st=self.store(id,s)
        await st.wait_compression()
        if self.settings.get(id) is not s or not self.valid(id,identity):raise PreflightRejected('stale_internal_work')
        moment=action.get('autonomousMoment') is True
        main_activation=moment or bool(action.get('continuation'))
        main_scope=None
        if main_activation:
            if st.active or getattr(st,'waiting',0) or id in self.running or self.provider(id) is None:
                return {'status':'blocked','reason':'foreground_work_busy' if st.active or getattr(st,'waiting',0) or id in self.running else 'model_unavailable','summary':'主模型未空闲或不可用，本次未激活','effects':[],'evidence':[],'unfinished':[],'usage':dict(ZERO_USAGE),'continuationAccepted':False}
            from activity_context import scope_for
            try:main_scope=scope_for(st,self.policies.get(id,{}).get('config',{}))
            except ValueError as error:return {'status':'blocked','reason':str(error),'summary':'未能确定现有主会话，本次未激活','effects':[],'evidence':[],'unfinished':[],'usage':dict(ZERO_USAGE),'continuationAccepted':False}
        continuation=bool(action.get('continuation') or (action.get('requestSource') or {}).get('parentActionId'))
        model_origin=(action.get('requestSource') or {}).get('origin')=='model'
        if moment and action.get('activationTrigger','activity')=='activity' and not self.policies.get(id,{}).get('config',{}).get('activityEnabled',True):raise PreflightRejected('activity_disabled')
        if model_origin and not continuation and not s['config'].get('proactive'):
            return {'status':'blocked','summary':'自主模式已关闭，未发起自主研究模型调用','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE)}
        identity={**identity,'_model_origin':model_origin,'_continuation':continuation}
        if not self.valid(id,identity):raise PreflightRejected('stale_internal_work')
        p=self.policies[id];kind=action['kind'];target=action.get('target','');candidates=self.candidates(id)
        import agreement_state
        if not st.pipeline.handoff_current(action.get('goalId')):
            return {'status':'canceled','summary':'原请求已取消，未继续执行','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE)}
        if not agreement_state.task_current(st.db,(action.get('requestSource') or {}).get('agreement')):
            return {'status':'canceled','summary':'约定已取消或修订，旧版本任务未执行','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE)}
        identity={**identity,'kind':kind}
        request=st.pipeline.request_reply(identity,action.get('goalId'),action.get('requestSource'),identity['id'],current_lease=s) if kind=='message' else None
        if request and request['scope']!=target:request=None
        if request and target.startswith('group:') and s['config'].get('groups') and target[6:] not in s['config']['groups']:request=None
        previous=st.pipeline.internal_result(identity['id'])
        if previous:
            st.pipeline.bind_internal(None,s,identity,target if kind=='message' else 'self',action['objective'])
            return previous['result'] or {'status':'unknown','summary':'已有动作尚未完整结算；禁止重新执行','effects':previous['effects'],'evidence':[],'unfinished':[action['objective']],'usage':{'modelCalls':0,'toolCalls':0,'tokens':0}}
        if kind=='message' and action.get('requestSource') and not request and not continuation:return {'status':'blocked','summary':'原请求回复许可无效或已使用；不能降级为主动发送重试','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE),'contextScope':target}
        candidate=next((c for c in candidates if c['scope']==target),None)
        if request:
            row=st.db.execute('SELECT self_id,last_user FROM contacts WHERE scope=?',(target,)).fetchone()
            if row:candidate={'scope':target,'selfId':row[0],'lastUserAt':row[1],'requested':True}
            else:request=None
        if kind=='message' and not candidate:return {'status':'blocked','summary':'当前对象不在允许且到期的真实联系人中','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE),'contextScope':target}
        b=s['config'].get('behavior') or {}
        if kind=='profile_signature' and not (b.get('qqProfile') and b.get('autoSignature',True)):raise ValueError('inner_signature_disabled')
        if kind=='qzone_publish' and not b.get('qzone'):raise ValueError('inner_qzone_disabled')
        if p['config']['rollout']=='shadow' and kind!='research':return {'status':'completed','summary':'影子模式：已记录对外意图，未实际发送或执行','effects':[],'evidence':[],'unfinished':[],'shadow':True,'usage':dict(ZERO_USAGE)}
        if kind!='research' and not self.valid(id,identity,effect=True):raise ValueError('effect_permit_expired')
        scope=target if kind=='message' else main_scope if main_activation and main_scope!='bot_shared' else 'self';self_id=candidate['selfId'] if candidate else str(s.get('expectedUin') or '')
        if not self_id:
            # Older valid QQ sessions may have no persisted expectedUin. Bind
            # self work to this member's real protocol, never a model argument
            # or another member's contact. Tools still recheck login identity.
            platform=next(p for p in self.core.platform_manager.get_insts() if p.meta().id==id)
            try:login=await asyncio.wait_for(platform.bot.get_login_info(),5)
            except asyncio.CancelledError:raise
            except Exception:return {'status':'blocked','summary':'当前绑定协议的QQ登录身份读取失败，未执行任务。','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE),'contextScope':scope}
            if not self.valid(id,identity):return {'status':'canceled','summary':'读取QQ身份期间任务身份或配置已变化，未执行任务。','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE),'contextScope':scope}
            self_id=str(login.get('user_id') or '') if isinstance(login,dict) else ''
            if not re.fullmatch(r'[1-9][0-9]{4,19}',self_id):return {'status':'blocked','summary':'当前绑定协议未返回有效QQ身份，未执行任务。','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE),'contextScope':scope}
        event=self.make_event(id,identity,scope if scope!='self' else 'private:0',action['objective'],self_id)
        if main_activation:
            event.set_extra('_hosted_main_activation',True)
            event.set_extra('_hosted_main_journal_scope',main_scope)
        bound=st.pipeline.bind_internal(event,s,identity,scope,action['objective'])
        if isinstance(bound,dict):
            result=st.pipeline.internal_result(identity['id']);return result['result'] or {'status':'unknown' if result['effects'] else 'canceled','summary':'同一动作已有执行记录，禁止重新执行','effects':result['effects'],'evidence':[],'unfinished':[action['objective']]}
        work=bound;work.stage('observe');work.stage('admission')
        event.set_extra('_hosted_autonomous_action',action)
        if candidate:event.set_extra('_hosted_internal_last_user',candidate['lastUserAt'])
        if request:
            if not st.pipeline.request_reply(identity,action.get('goalId'),action.get('requestSource'),identity['id'],reserve=True,current_lease=s):
                feedback={'status':'blocked','summary':'原请求的回复许可已被其他动作持有','effects':[],'evidence':[],'unfinished':[action['objective']],'usage':dict(ZERO_USAGE)};st.pipeline.finish_internal(work,feedback);return feedback
            event.set_extra('_hosted_requested_reply',True)
        if kind=='message' and not request:
            if not autonomy.reserve_send(st.db,scope,identity['id'],s['config']):
                feedback={'status':'blocked','summary':'现有主动发送额度已用完','effects':[],'evidence':[],'unfinished':[],'usage':dict(ZERO_USAGE)};st.pipeline.finish_internal(work,feedback);return feedback
            event.set_extra('_hosted_autonomy_reservation',identity['id'])
            now=time.time();day=initiative.datetime.fromtimestamp(now,initiative.ZONE).date().isoformat()
            event.set_extra('_hosted_timer_item',{'scope':scope,'reserved_at':now})
            st.db.execute('UPDATE contacts SET last_attempt=?,unanswered=unanswered+1 WHERE scope=?',(now,scope))
            st.db.execute('INSERT INTO deliveries VALUES(?,?,1,?) ON CONFLICT(scope,day) DO UPDATE SET count=count+1,last_sent=excluded.last_sent',(scope,day,now));st.db.commit()
        memory_epoch=st.reflection.epoch()
        result={'status':'blocked','reason':'task_not_started','text':'','usage':{'tokens':0,'modelCalls':0,'toolCalls':0},'evidence':[]}
        failure=''
        try:
            if not self.valid(id,identity) or st.reflection.epoch()!=memory_epoch:raise asyncio.CancelledError('stale internal memory context')
            work.stage('context');context=self.prepare_context(id,event,scope,action['objective'])
            tools=self.task_tools(id,identity,scope,event)
            context_object=SimpleNamespace(event=event)
            prior=data.get('feedback')
            # A prior conversation's dispatch failure is not this task's admission
            # result, even when the two tasks contact the same person. Current chat
            # history comes from ContextBuilder; only internal research may supply
            # separate findings to a newly admitted outgoing conversation.
            if kind=='message' and prior and prior.get('contextScope')!='self':prior=None
            # The provider hook assembles the same ContextBuilder result once.
            prompt=json.dumps({'task':action,'priorFeedback':prior,'allowedContacts':self.candidates(id)},ensure_ascii=False)
            if moment:prompt+='\n现在是一次自主活动时刻。请根据当前状态、记忆、经历、关系、目标及可用工具，结合当前目标为何形成、已确认成果、实际阻塞和最近反馈，判断有没有值得做的事情。可以继续、修订、结束或放下目标，也可形成后续目标或休息；不必每次从零策划或制造任务。放下不等于完成，休息不取消已有任务。最终文字是内部活动记录，不会自动发给任何人。'
            system=s['config'].get('systemPrompt','')+'\n这是平台授权的内部任务，不是主人发来一条新消息。sender=0是服务身份，不拥有主人权限；真实联系人是recipient。以当前任务为范围，结合已有反馈选择下一步；自主研究不冒充主人委托，自己的新观点可由真实经历形成，不虚构刚发生的事件。工具返回才是实际结果。对话任务最后输出要实际发给目标的一轮自然内容，允许[MEME:真实候选id]及[QQ_BREAK]，不假装已经发送。研究任务返回有证据的结论、未完成事项，不能用虚构检索结果。没有工具能力时如实说明。'
            system+='\n'+bot_pipeline.EXECUTION_DISCLOSURE_POLICY
            if main_activation:
                system=s['config'].get('systemPrompt','')
                prompt=json.dumps({'kind':'autonomous_activation' if moment else 'autonomous_tool_continuation','activationId':identity['id'],
                    **({'continuation':action['continuation']} if action.get('continuation') else {}),
                    'meaning':'平台激活，不是用户消息。结合原主对话、人格、目标和真实回执自主行动、修订目标或休息，不必制造任务；最终文字仅记录当前思考，不代表已发QQ。'},ensure_ascii=False,separators=(',',':'))
            if kind=='message':system+='\n本轮聊天任务及固定会话已通过当前初始准入，最终投递仍由公共管线实时校验并记录回执。历史任务的失败不是本轮的发送状态；你的最终内容将作为消息发给当前对象，不要替平台编造投递结果。'
            if kind=='research' and not main_activation:system+='\n本任务为内部研究，最后只输出JSON：{"status":"completed或blocked或partial","summary":"已证实的结果或受阻原因","unfinished":["实际未完成项"]}。这是当前任务步骤的结果，不是长期目标的终态。检索成功但无匹配时，准确报告无匹配；这不表示长期目标已实现。工具拒绝、失败或未执行时必须blocked或partial，不能用通用常识替代任务完成。引用和证据以平台实际工具回执为准。摘要中区分工具实际返回的内容、未经核验的推测与缺失材料；只得到目录、标题、摘要或检索元数据时，不得声称已读取完整内容或验证其中事实，获取具体知识所需的原文或媒体未读取则列入unfinished。'
            result=await self.invoke(id,identity,prompt,system,tools=tools,context=context_object)
            if self.settings.get(id) is s and result['status']=='completed' and kind=='message' and result['text'].strip():
                from astrbot.api.event import MessageChain
                from astrbot.core.message.components import Plain
                from astrbot.core.platform.astr_message_event import current_message_event
                token=current_message_event.set(event)
                try:await event.send(MessageChain([Plain(result['text'])]))
                finally:current_message_event.reset(token)
        except asyncio.CancelledError:failure='task_canceled'
        except Exception as error:
            from native_internal import WorkBudgetExceeded,WorkStopped
            failure=str(error) if isinstance(error,(WorkBudgetExceeded,WorkStopped)) else type(error).__name__
        finally:
            if self.settings.get(id) is s:
                initiative.release_unstarted(st.db,event.get_extra('_hosted_timer_item') or {},event.get_extra('_hosted_autonomy_reservation'))
        if self.settings.get(id) is not s:
            # drain() keeps this original store alive until the task returns.
            # Only settle its immutable action; never write a replacement
            # member's memory, metrics or relationship state.
            import external_action_budget
            effects=await external_action_budget.effects(s,identity,st.pipeline.internal_result(identity['id'])['effects'])
            status='partial' if any(e['status']=='confirmed' for e in effects) else 'unknown' if effects else 'canceled'
            feedback={'status':status,'summary':'成员已卸载；原动作回执已保留，未派发部分已撤销。','stopReason':'member_unloaded','effects':effects,'evidence':result['evidence'],'unfinished':['核对原任务剩余部分；不得重复已经确认的操作。'] if effects else [action['objective']],'usage':result['usage'],'contextScope':scope,'toolResults':result.get('toolResults',[]),'checkpoints':result.get('checkpoints',[])}
            if kind=='message':feedback['response']='' if event.get_extra('_hosted_final_reply_suppressed') else result['text'][:1600]
            st.pipeline.finish_internal(work,feedback)
            return feedback
        import external_action_budget
        effects=await external_action_budget.effects(s,identity,st.pipeline.internal_result(identity['id'])['effects'])
        status='canceled' if failure=='task_canceled' else 'blocked' if failure else result['status']
        if any(e['status']=='unknown' for e in effects):status='partial' if any(e['status']=='confirmed' for e in effects) else 'unknown'
        elif (failure or result['status']!='completed') and any(e['status']=='confirmed' for e in effects):status='partial'
        elif kind=='message' and effects and work.confirmed_messages<work.planned_messages:status='partial'
        elif kind!='research' and not effects:status='blocked'
        summary=result['text'][:1600] or result['reason']
        if kind=='message':summary='协议投递：确认'+str(sum(e['status']=='confirmed' for e in effects))+'，不明'+str(sum(e['status']=='unknown' for e in effects))+'；投递状态为'+status+'。是否完成用户任务需另对照实际内容。'
        unfinished=[] if status=='completed' else [action['objective']]
        # An internal tool task can perform an authorized QQ operation. Its
        # real protocol receipt is evidence too, not an unverified model claim.
        for effect in effects:
            if effect['status']=='confirmed':result['evidence'].append({'source':'qq_protocol_receipt','reference':effect['id']})
        if kind=='research' and not main_activation and status=='completed':
            try:
                reported=json_object(result['text'])
                if reported.get('status') not in ('completed','blocked','partial') or not isinstance(reported.get('summary'),str) or not isinstance(reported.get('unfinished'),list):raise ValueError('invalid_task_feedback')
                status=reported['status'];summary=reported['summary'][:1600];unfinished=[str(x)[:500] for x in reported['unfinished'][:6]]
                if not any(x['status']=='returned' for x in result.get('toolResults',[])) or not result['evidence']:
                    status='blocked';unfinished=unfinished or [action['objective']]
                elif status=='completed' and any(x['status'] in ('failed','unavailable') for x in result.get('toolResults',[])):
                    status='partial';unfinished=unfinished or [action['objective']]
            except (ValueError,TypeError):status='blocked';summary='内层未返回有效的结构化研究结果；实际工具回执保留。';unfinished=[action['objective']]
        if kind!='research' and not effects:summary='未确认任何对外操作。'+(failure or result['reason'])
        if status=='partial' and effects and all(e['status']=='confirmed' for e in effects) and (failure or result['status']!='completed'):
            summary='已有'+str(len(effects))+'项对外操作获得协议确认；后续计算已停止（'+(failure or result['reason'])+'）。不得重复执行已确认操作。'
            unfinished=['核对已有回执并决定后续步骤；已确认的对外操作不可重复执行。']
        feedback={'status':status,'summary':summary,'stopReason':failure or result['reason'],'evidence':result['evidence'],'evidenceLimits':evidence_limits(result['evidence']),'effects':effects,'unfinished':unfinished,'usage':result['usage'],'contextScope':scope,'toolResults':result.get('toolResults',[]),'checkpoints':result.get('checkpoints',[])}
        feedback['deliveryComplete']=bool(kind=='message' and status=='completed' and effects and work.planned_messages>0 and work.confirmed_messages==work.planned_messages and all(e['status']=='confirmed' for e in effects))
        if kind=='message':feedback['response']='' if event.get_extra('_hosted_final_reply_suppressed') else result['text'][:1600]
        if kind=='research' and status=='completed' and feedback['evidence']:
            # Research outcomes stay in the authoritative action ledger and in
            # execution-memory recall, both source-attributed. They are tool
            # task results, never persona self facts, and existing rows of
            # this trusted origin are excluded from every facts recall pool.
            self.metrics.setdefault(id,{}).setdefault('researchResultsLedgered',0)
            self.metrics[id]['researchResultsLedgered']+=1
        feedback['task']={'kind':kind,'target':target,'objective':action['objective'][:400],'sourceScope':action.get('sourceScope') or (action.get('requestSource') or {}).get('scope'),'outerDecision':action.get('outerDecision')}
        feedback['finishedAt']=int(time.time()*1000)
        if action.get('continuation'):
            feedback['continuationAccepted']=bool(event.get_extra('_hosted_main_request_staged')) if main_activation else result.get('usage',{}).get('modelCalls',0)>0
        if moment:
            feedback['autonomousMoment']=True
            feedback['restSeconds']=event.get_extra('_hosted_activity_rest')
        st.pipeline.finish_internal(work,feedback,action.get('goalId'));st.pipeline.finish(work,self.metrics.setdefault(id,{}))
        if kind=='message' and not main_activation:
            binding=getattr(self.core,'_hosted_runtime_binding',None)
            if binding:binding['runtime']['settle_work_round'](st,event,work,s)
        if main_activation and event.get_extra('_hosted_activation_admitted'):
            from astrbot.core.platform.astr_message_event import current_message_event
            b=s['config'].get('behavior') or {}
            st.reflection.maybe_start(main_scope,{**b,'_persona':s['config'].get('systemPrompt','')},self.provider(id),lambda:self.settings.get(id) is s and id not in self.core.event_bus.stopped_platforms,self.metrics.setdefault(id,{}),current_message_event)
            self.care.track(st,st.reflection.task)
        return feedback

    async def handle(self,id,operation,data):
        if operation!='cancel' and id in self.settings:
            lease=self.settings[id];st=self.store(id,lease)
            await st.wait_compression()
            if self.settings.get(id) is not lease or getattr(st,'_closing',False):raise ValueError('stale_internal_work')
        if operation=='policy':return self.install(id,data)
        if operation=='snapshot':return self.snapshot(id,ledger_only=data.get('ledgerOnly') is True)
        if operation=='handoff_ack':
            p=self.policies.get(id)
            if not p or p['lease'] is not self.settings.get(id) or data.get('epoch')!=p['epoch'] or not self.permit_current(p):raise ValueError('stale_handoff_ack')
            return self.store(id,self.settings[id]).pipeline.acknowledge_handoff(p['epoch'],data['id'],data['receipt'])
        if operation=='execute':
            if id in self.tasks:raise ValueError('background_work_busy')
            self.tasks[id]=asyncio.current_task()
            try:return await self.execute(id,data)
            except PreflightRejected as error:return {'status':'canceled','reason':str(error),'preflightReason':self.validation_error(id,data.get('work') or {}),'summary':'执行前身份已失效，未调用模型或协议','effects':[],'evidence':[],'unfinished':[],'usage':dict(ZERO_USAGE)}
            finally:self.tasks.pop(id,None)
        if operation=='receipt':
            if id not in self.tasks and self.store(id,self.settings[id]).db.execute("SELECT 1 FROM qq_favorite_writes WHERE action=? AND state='unknown' LIMIT 1",(data['id'],)).fetchone():
                from qq_favorite_tools import reconcile
                s=self.settings[id];st=self.store(id,s)
                platform=next((p for p in self.core.platform_manager.get_insts() if p.meta().id==id),None)
                if platform:await reconcile(st.db,platform.bot,str(s.get('expectedUin') or ''),data['id'],lambda:self.settings.get(id) is s and id not in self.core.event_bus.stopped_platforms)
            receipt=self.store(id,self.settings[id]).pipeline.internal_result(data['id'])
            return {**(receipt or {'effects':[]}),'exists':receipt is not None,'active':id in self.tasks}
        if operation=='cancel':self.cancel(id,invalidate=True);return {'ok':True}
        raise ValueError('unknown_autonomy_operation')

    def close(self):
        self.closed=True
        for id in list(self.running):self.cancel(id)
        self.policies.clear();self.events.clear();self.feedbacks.clear();self.pending_ambient.clear()
