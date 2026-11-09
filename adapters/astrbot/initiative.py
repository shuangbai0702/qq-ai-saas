"""Companion scheduling: local eligibility, one ordinary AstrBot model turn.
Mechanism references: DSH speak-up, Cortico QQ proactive and AstrBot dsh-initiate.
No secondary model, no implicit recipients, no separate web server.
"""
import time,json,re,random,asyncio,uuid
import companion_state
import autonomy
from message_layout import split_messages
from datetime import datetime,timezone,timedelta
ZONE=timezone(timedelta(hours=8))
# Internal proactive anchor: a clearly platform-authored event, never the
# recipient's words and never a "say something" order. Silence ([NO_REPLY])
# is the default when nothing is genuinely worth sharing.
PROACTIVE_ANCHOR='[平台内部主动时刻]'
PROACTIVE_PROMPT=PROACTIVE_ANCHOR+'\n{"kind":"proactive_moment","meaning":"平台调度的一次主动机会；没有用户新消息，本条不是对方发言。有真实值得分享或对方会感兴趣的内容时，才简短自然地说一两句；话题若来自自己的研究或兴趣，以自己的新想法自然分享，不能改称对方曾提出；只有真实对方原话才能归于对方。没有合适内容就只输出[NO_REPLY]，不为找话题打扰对方。"}'

def quiet(cfg,now):
    b=cfg.get('behavior') or {};hour=datetime.fromtimestamp(now,ZONE).hour
    start,end=b.get('quietStart',autonomy.BEHAVIOR_DEFAULTS['quietStart']),b.get('quietEnd',autonomy.BEHAVIOR_DEFAULTS['quietEnd'])
    return start<=hour<end if start<end else (hour>=start or hour<end) if start!=end else False

def initialize(db):
    companion_state.initialize(db)
    autonomy.initialize(db)
    db.executescript('''CREATE TABLE IF NOT EXISTS initiative_checks(scope TEXT PRIMARY KEY,checked REAL,chance REAL,allowed INTEGER);''')
    db.execute('CREATE TABLE IF NOT EXISTS initiative_pauses(scope TEXT PRIMARY KEY,until REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS initiative_schedule(scope TEXT PRIMARY KEY,anchor REAL,minimum INTEGER,maximum INTEGER,due REAL)')
    db.executescript('''CREATE TABLE IF NOT EXISTS contacts(scope TEXT PRIMARY KEY,subject TEXT,self_id TEXT,last_user REAL,last_bot REAL DEFAULT 0,last_attempt REAL DEFAULT 0,unanswered INTEGER DEFAULT 0,opted_out INTEGER DEFAULT 0,last_text TEXT DEFAULT '',last_output TEXT DEFAULT '');''')

def incoming(db,scope,subject,self_id,text,now,directed=True):
    # Chat is evidence for the existing model calls. A quoted refusal, goodbye
    # or a bystander's wording cannot permanently disable an entire session.
    # opted_out is retained for explicit operator controls and legacy review.
    db.execute('INSERT INTO contacts(scope,subject,self_id,last_user,last_text,opted_out) VALUES(?,?,?,?,?,0) ON CONFLICT(scope) DO UPDATE SET subject=excluded.subject,self_id=excluded.self_id,last_user=excluded.last_user,last_text=excluded.last_text,unanswered=CASE WHEN ? THEN 0 ELSE contacts.unanswered END',(scope,subject,self_id,now,text[:500],int(directed)))
    if directed:
        db.execute('DELETE FROM initiative_pauses WHERE scope=?',(scope,))
    db.execute('DELETE FROM initiative_pauses WHERE until<=?',(now,))
    db.commit()

def paused(db,scope,now):
    row=db.execute('SELECT until FROM initiative_pauses WHERE scope=?',(scope,)).fetchone()
    return bool(row and row[0]>now)

def outgoing(db,scope,text,now):
    db.execute('UPDATE contacts SET last_bot=?,last_output=? WHERE scope=?',(now,text[:500],scope));db.commit()

def sweep_reason(db,cfg,now):
    """Transparent reason for a proactive sweep that produced nothing (D7.1).

    The daily send budget being exhausted is encoded separately from a
    genuinely empty candidate set, and the owner path's status stays explicit.
    This reports facts only: no owner quota is added and no default changes.
    """
    b=cfg.get('behavior') or {}
    if b.get('enabled',True) is False or not (cfg.get('proactive') or b.get('ownerContactEnabled',autonomy.BEHAVIOR_DEFAULTS['ownerContactEnabled'])):return {'reason':'disabled'}
    if quiet(cfg,now):return {'reason':'quiet_hours'}
    day=datetime.fromtimestamp(now,ZONE).date().isoformat()
    limit=b.get('dailyProactiveLimit',10)
    total=db.execute('SELECT coalesce(sum(count),0) FROM deliveries WHERE day=?',(day,)).fetchone()[0]
    budget={'used':int(total),'limit':limit}
    if total>=limit:return {'reason':'budget_exhausted','budget':budget,'owner':'not_considered_daily_budget_exhausted'}
    if autonomy.owner_delivery_candidate(db,cfg,now):return {'reason':'ok_owner','budget':budget}
    if not cfg.get('proactive') or not companion_state.targets(db,cfg,now):return {'reason':'no_candidates','budget':budget,'owner':'not_due'}
    return {'reason':'no_candidates','budget':budget,'owner':'not_due'}


def candidates(db,cfg,now,rng=random.random):
    b=cfg.get('behavior') or {};targets=set(companion_state.targets(db,cfg,now))
    if b.get('enabled',True) is False or not (cfg.get('proactive') or b.get('ownerContactEnabled',autonomy.BEHAVIOR_DEFAULTS['ownerContactEnabled'])) or quiet(cfg,now):return []
    day=datetime.fromtimestamp(now,ZONE).date().isoformat()
    total=db.execute('SELECT coalesce(sum(count),0) FROM deliveries WHERE day=?',(day,)).fetchone()[0]
    if total>=b.get('dailyProactiveLimit',10):return []
    owner_item=autonomy.owner_delivery_candidate(db,cfg,now)
    if owner_item:return [owner_item]
    if not cfg.get('proactive'):return []
    if not targets:return []
    rows=db.execute('SELECT scope,subject,self_id,last_user,last_bot,last_attempt,unanswered,opted_out,last_text,last_output FROM contacts WHERE scope IN ('+','.join('?' for _ in targets)+') ORDER BY last_user DESC LIMIT 50',tuple(targets)).fetchall()
    result=[]
    for r in rows:
        scope,subject,self_id,last_user,last_bot,last_attempt,unanswered,opted_out,last_text,last_output=r
        if b.get('ownerContactEnabled',autonomy.BEHAVIOR_DEFAULTS['ownerContactEnabled']) and scope=='private:'+str(b.get('ownerUin') or ''):continue
        if scope.startswith('group:') and not autonomy.group_allowed(cfg,scope):continue
        selected=companion_state.select_member(db,cfg,scope,now,allow_without_topic=b.get('proactiveModelSchedule',False))
        automatic=scope not in b.get('proactiveTargets',[])
        if automatic and not selected:continue
        if scope not in targets or opted_out or paused(db,scope,now) or unanswered>=b.get('maxUnanswered',4):continue
        if scope.startswith('group:') and cfg.get('groups') and scope[6:] not in cfg['groups']:continue
        if now-last_user<b.get('silenceSeconds',1800) or now-max(last_bot,last_attempt)<b.get('proactiveIntervalSeconds',3600):continue
        topic=selected['topic'] if selected else ''
        if b.get('proactiveModelSchedule'):
            plan=db.execute('SELECT due,topic,allowed FROM companion_plans WHERE scope=? AND anchor=?',(scope,max(last_user,last_bot,last_attempt))).fetchone()
            if not plan or not plan[2] or now<plan[0]:continue
            topic=plan[1]
            planned=db.execute('SELECT subject,seen FROM companion_plan_members WHERE scope=? AND anchor=?',(scope,max(last_user,last_bot,last_attempt))).fetchone()
            if planned:
                # Membership/consent must still be eligible at send time, not
                # merely present when the model picked a person half an hour ago.
                eligible=companion_state.select_members(db,cfg,scope,now,allow_without_topic=True)
                selected=next((m for m in eligible if m['subject']==planned[0] and m['seen']==planned[1]),None)
                if not selected:continue
        if b.get('proactiveRandomInterval') and not b.get('proactiveModelSchedule'):
            minimum=max(30,min(600,b.get('proactiveMinMinutes',30)));maximum=max(minimum,min(600,b.get('proactiveMaxMinutes',600)))
            anchor=max(last_user,last_bot,last_attempt)
            schedule=db.execute('SELECT anchor,minimum,maximum,due FROM initiative_schedule WHERE scope=?',(scope,)).fetchone()
            if not schedule or schedule[:3]!=(anchor,minimum,maximum):
                due=anchor+(minimum+(maximum-minimum)*rng())*60
                db.execute('INSERT OR REPLACE INTO initiative_schedule VALUES(?,?,?,?,?)',(scope,anchor,minimum,maximum,due))
                db.execute('DELETE FROM initiative_schedule WHERE anchor<?',(now-8*86400,));db.commit()
            else:due=schedule[3]
            if now<due:continue
        if now-last_user>7*86400:continue
        # Polling is not a new probability trial: keep one decision for the
        # configured interval, including across restarts. Successful candidates
        # remain available while QQ reconnects; reserve() fences actual attempts.
        chance=1 if b.get('proactiveModelSchedule') else b.get('proactiveChance',0.25)
        check=db.execute('SELECT checked,chance,allowed FROM initiative_checks WHERE scope=?',(scope,)).fetchone()
        if check and check[1]==chance and 0<=now-check[0]<b.get('proactiveIntervalSeconds',3600):allowed=bool(check[2])
        else:
            allowed=chance>0 and rng()<chance
            db.execute('INSERT INTO initiative_checks VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET checked=excluded.checked,chance=excluded.chance,allowed=excluded.allowed',(scope,now,chance,int(allowed)))
            db.execute('DELETE FROM initiative_checks WHERE checked<?',(now-8*86400,));db.commit()
        if not allowed:continue
        result.append({'scope':scope,'subject':subject,'self_id':self_id,'last_text':last_text,'last_output':last_output,'last_user':last_user,'topic':topic,'contact_subject':selected['subject'] if selected else None,'contact_seen':selected['seen'] if selected else None})
        break # bounded to one candidate per bot per sweep
    return result

def reserve(db,cfg,item,now):
    day=datetime.fromtimestamp(now,ZONE).date().isoformat();b=cfg.get('behavior') or {}
    if not b.get('enabled',True) or quiet(cfg,now) or not (b.get('ownerContactEnabled',autonomy.BEHAVIOR_DEFAULTS['ownerContactEnabled']) if item.get('owner_contact') else cfg.get('proactive')):return False
    # Reserve before enqueue. Failure consumes an attempt instead of retry-spending.
    with db:
        total=db.execute('SELECT coalesce(sum(count),0) FROM deliveries WHERE day=?',(day,)).fetchone()[0]
        if total>=b.get('dailyProactiveLimit',10):return False
        r=db.execute('SELECT last_user,opted_out,unanswered FROM contacts WHERE scope=?',(item['scope'],)).fetchone()
        if not r or r[0]!=item['last_user'] or r[1] or paused(db,item['scope'],now) or r[2]>=b.get('maxUnanswered',4):return False
        if item.get('owner_contact'):
            if not autonomy.owner_delivery_current(db,cfg,item,now):return False
            # A positive decision is consumed once, even if the reply model
            # stays silent. Refunding an unsent message must not create a
            # new model attempt on every five-second scheduler sweep.
            if not db.execute('UPDATE autonomy_owner SET allowed=2 WHERE owner=? AND checked=? AND version=? AND allowed=1',
                              (str(b.get('ownerUin') or ''),item['owner_checked'],item['owner_version'])).rowcount:return False
        if item.get('contact_subject'):
            if not companion_state.member_current(db,item['scope'],item['contact_subject'],item['contact_seen'],now):return False
            changed=db.execute('UPDATE companion_members SET attempt=?,unanswered=unanswered+1 WHERE scope=? AND subject=? AND unanswered<? AND attempt<?',(now,item['scope'],item['contact_subject'],b.get('maxUnanswered',4),now-b.get('proactiveIntervalSeconds',3600))).rowcount
            if not changed:return False
        db.execute('UPDATE contacts SET last_attempt=?,unanswered=unanswered+1 WHERE scope=?',(now,item['scope']))
        db.execute('INSERT INTO deliveries VALUES(?,?,1,?) ON CONFLICT(scope,day) DO UPDATE SET count=count+1,last_sent=excluded.last_sent',(item['scope'],day,now))
    return True


def release_unstarted(db,item,key):
    """Refund only a timer proven not to have reached protocol dispatch."""
    at=item.get('reserved_at');scope=item.get('scope')
    if at is None or not scope or not key:return
    day=datetime.fromtimestamp(at,ZONE).date().isoformat()
    with db:
        if not db.execute("UPDATE autonomy_sends SET state='released' WHERE event=? AND state='pending'",(key,)).rowcount:return
        changed=db.execute('UPDATE contacts SET unanswered=max(0,unanswered-1) WHERE scope=? AND last_attempt=?',(scope,at)).rowcount
        if changed:
            db.execute('UPDATE deliveries SET count=max(0,count-1) WHERE scope=? AND day=? AND last_sent=?',(scope,day,at))
            if item.get('contact_subject'):db.execute('UPDATE companion_members SET unanswered=max(0,unanswered-1) WHERE scope=? AND subject=? AND attempt=?',(scope,item['contact_subject'],at))

def split_text(text,limit=4):
    return split_messages(text,limit)

async def resume_scope_input(core,id,lease,store,settings):
    """Resume one durable unprocessed ingress, never an unknown external effect."""
    # Protocol redelivery may already have a terminal original Work. Reconcile
    # only queue bookkeeping; never manufacture a response or replay effects.
    with store.db:
        store.db.execute("""UPDATE pipeline_work_inputs AS i SET status=CASE WHEN EXISTS
          (SELECT 1 FROM pipeline_events e WHERE e.scope=i.scope AND e.subject=i.subject AND e.event=i.event
           AND e.status IN ('sent','silent','superseded','blocked','error','interrupted','combined') AND e.attempted=e.confirmed)
          THEN 'settled_duplicate' ELSE 'blocked_unknown' END
          WHERE i.status='queued' AND NOT EXISTS (SELECT 1 FROM pipeline_scope_work w WHERE w.scope=i.scope)
          AND EXISTS (SELECT 1 FROM pipeline_events e WHERE e.scope=i.scope AND e.subject=i.subject AND e.event=i.event)""")
        store.db.execute("""UPDATE pipeline_work_inputs AS i SET retry_count=coalesce(retry_count,0)+1,
          status=CASE WHEN coalesce(retry_count,0)>=4 THEN 'blocked_recovery' ELSE 'pending' END
          WHERE i.status='queued' AND coalesce(i.retry_at,0)<?
          AND NOT EXISTS (SELECT 1 FROM pipeline_scope_work w WHERE w.scope=i.scope)
          AND NOT EXISTS (SELECT 1 FROM pipeline_events e WHERE e.scope=i.scope AND e.subject=i.subject AND e.event=i.event)""",(time.time(),))
        for failed_scope,failed_event,count in store.db.execute("SELECT scope,event,retry_count FROM pipeline_work_inputs WHERE status='blocked_recovery'").fetchall():
            store.reflection.checkpoint_issue('bot_shared',0,'input_recovery:'+failed_scope+':'+failed_event,{'scope':failed_scope,'event':failed_event,'reason':'ingress_not_accepted','state':'blocked_recovery','retry_count':count,'external_replay':False})
    row=store.db.execute("SELECT scope,event,payload,observed_at,retry_count FROM pipeline_work_inputs i WHERE status='pending' AND coalesce(retry_at,0)<=? AND NOT EXISTS (SELECT 1 FROM pipeline_scope_work w WHERE w.scope=i.scope) ORDER BY created LIMIT 1",(time.time(),)).fetchone()
    if not row:return False
    scope,event_id,payload,observed_at,retries=row
    platform=next((p for p in core.platform_manager.get_insts() if p.meta().id==id),None)
    if not platform or settings.get(id) is not lease or id in core.event_bus.stopped_platforms:return False
    from aiocqhttp import Event
    with store.db:
        if not store.db.execute("UPDATE pipeline_work_inputs SET status='queued',retry_at=? WHERE scope=? AND event=? AND status='pending'",(time.time()+30,scope,event_id)).rowcount:return False
    try:
        raw=Event.from_payload(json.loads(payload));message=await asyncio.wait_for(platform.convert_message(raw),5)
        if message is None or settings.get(id) is not lease:raise RuntimeError('scope_resume_stale')
        # The SDK conversion stamps replay time. Reuse the first trusted
        # observation instead; redelivery must not change protocol identity.
        if observed_at is None:
            from context_protocol import source
            previous=store.db.execute('SELECT message FROM fishbot_shared_inputs WHERE scope=? AND event=?',(scope,event_id)).fetchone()
            origin=source(json.loads(previous[0])) if previous else None
            observed_at=origin.get('at') if origin else None
        if not isinstance(observed_at,(int,float)) or observed_at<=0:
            with store.db:store.db.execute("UPDATE pipeline_work_inputs SET status='blocked_evidence' WHERE scope=? AND event=?",(scope,event_id))
            raise ValueError('scope_resume_original_time_missing')
        message.timestamp=observed_at
        platform.commit_event(platform.create_event(message))
        return True
    except BaseException as error:
        cancelled=isinstance(error,asyncio.CancelledError)
        retries=(retries or 0)+(not cancelled)
        with store.db:
            store.db.execute("UPDATE pipeline_work_inputs SET status=?,retry_count=?,retry_at=? WHERE scope=? AND event=? AND status='queued'",('blocked_recovery' if retries>=5 else 'pending',retries,time.time()+(min(600,15*2**min(retries,6)) if not cancelled else 0),scope,event_id))
            blocked=store.db.execute('SELECT status FROM pipeline_work_inputs WHERE scope=? AND event=?',(scope,event_id)).fetchone()[0]
            if blocked.startswith('blocked_'):
                store.reflection.checkpoint_issue('bot_shared',0,'input_recovery:'+scope+':'+event_id,{'scope':scope,'event':event_id,'reason':type(error).__name__,'state':blocked,'retry_count':retries,'external_replay':False})
        raise


def start(core,settings,store,metrics):
    async def sweep():
        from aiocqhttp import Event
        from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember,Group
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.message.components import Plain
        while True:
            await asyncio.sleep(5)
            for id,s in list(settings.items()):
                try:
                    brain=getattr(core,'_hosted_autonomous',None)
                    from bilibili_tools import resume_completion
                    st=store(id,s)
                    if await resume_scope_input(core,id,s,st,settings):continue
                    if not st.active and await resume_completion(core,id,s,st,settings):continue
                    if brain and brain.owns(id):continue
                    cfg=s['config']
                    b=cfg.get('behavior') or {}
                    if not (cfg.get('proactive') or b.get('groupParticipationEnabled',autonomy.BEHAVIOR_DEFAULTS['groupParticipationEnabled']) or b.get('ownerContactEnabled',autonomy.BEHAVIOR_DEFAULTS['ownerContactEnabled'])):continue
                    st=store(id,s)
                    if st.active:continue
                    provider=core.provider_manager.inst_map.get('provider-'+id) or (core.provider_manager.inst_map.get('platform-model') if len(settings)==1 else None)
                    from astrbot.core.platform.astr_message_event import current_message_event
                    live=lambda sid=id,lease=s:settings.get(sid) is lease and sid not in core.event_bus.stopped_platforms
                    pending=st.presence.pending(cfg,provider,live,time.time()) if live() else None
                    if pending:
                        platform=next((p for p in core.platform_manager.get_insts() if p.meta().id==id),None)
                        if not platform:continue
                        raw=Event.from_payload(json.loads(pending['payload']))
                        online=await asyncio.wait_for(platform.bot.get_status(self_id=str(raw.self_id)),3)
                        if not online.get('online') or not live() or st.active:continue
                        msg=await asyncio.wait_for(platform.convert_message(raw),5)
                        if not msg or not live() or st.active or not st.presence.claim(pending,cfg,provider,live,time.time()):continue
                        event=platform.create_event(msg);event.set_extra('_hosted_deferred_group',pending);event.set_extra('_hosted_scheduled_generation',s['generation'])
                        platform.commit_event(event)
                        m=metrics.setdefault(id,{});m['groupDeferredChecksQueued']=m.get('groupDeferredChecksQueued',0)+1
                        continue
                    if id not in core.event_bus.stopped_platforms:
                        st.planner.maybe_start(cfg,provider,lambda sid=id,lease=s:settings.get(sid) is lease and sid not in core.event_bus.stopped_platforms,current_message_event,metrics.setdefault(id,{}),lease=s)
                        task=st.planner.task
                        if task and not task.done() and not getattr(task,'_hosted_slot',False):
                            task._hosted_slot=True;st.active+=1
                            def finished(_,owner=st):owner.active-=1
                            task.add_done_callback(finished)
                        if getattr(core,'_hosted_care',None):core._hosted_care.track(st,task)
                    items=candidates(st.db,cfg,time.time())
                    if not items:
                        # Keep the empty-sweep reason visible instead of folding
                        # budget exhaustion into ordinary idleness.
                        metrics.setdefault(id,{})['proactiveSweepReason']=sweep_reason(st.db,cfg,time.time())
                        continue
                    platform=next((p for p in core.platform_manager.get_insts() if p.meta().id==id),None)
                    if not platform or id in core.event_bus.stopped_platforms:continue
                    item=items[0]
                    online=await asyncio.wait_for(platform.bot.get_status(self_id=item['self_id']),3)
                    if not online.get('online') or settings.get(id) is not s or id in core.event_bus.stopped_platforms:continue
                    if item.get('contact_subject'):
                        st.db.execute('INSERT OR REPLACE INTO companion_membership_checks VALUES(?,?,?)',(item['scope'],item['contact_subject'],time.time()));st.db.execute('DELETE FROM companion_membership_checks WHERE checked<?',(time.time()-86400,));st.db.commit()
                        member=await asyncio.wait_for(platform.bot.get_group_member_info(group_id=int(item['scope'][6:]),user_id=int(item['contact_subject']),no_cache=True),3)
                        if str(member.get('user_id'))!=item['contact_subject'] or settings.get(id) is not s:continue
                    now=time.time()
                    if quiet(cfg,now):continue
                    reservation=str(s.get('generation'))+'/timer/'+str(uuid.uuid4())
                    if not autonomy.reserve_send(st.db,item['scope'],reservation,cfg,now):continue
                    if not reserve(st.db,cfg,item,now):
                        autonomy.settle_send(st.db,reservation,'released');continue
                    group=item['scope'].startswith('group:');target=item['scope'].split(':',1)[1]
                    prompt=PROACTIVE_PROMPT
                    if item.get('topic'):prompt+='\n机器人联系规划选择的话题（不是对方发言，不能据此声称对方提过）：'+json.dumps(item['topic'],ensure_ascii=False)
                    raw=Event.from_payload({'time':int(now),'self_id':int(item['self_id']),'post_type':'message','message_type':'group' if group else 'private','sub_type':'normal' if group else 'friend','message_id':uuid.uuid4().int%(2**31),'user_id':int(item['subject']),'message':[{'type':'text','data':{'text':prompt}}],'raw_message':prompt,'sender':{'user_id':int(item['subject']),'nickname':'已授权会话'},**({'group_id':int(target)} if group else {})})
                    msg=AstrBotMessage();msg.type=MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE;msg.self_id=item['self_id'];msg.session_id=target;msg.message_id=str(raw['message_id']);msg.sender=MessageMember(user_id=item['subject'],nickname='已授权会话');msg.message=[Plain(prompt)];msg.message_str=prompt;msg.timestamp=int(now);msg.raw_message=raw
                    if group:msg.group=Group(group_id=target)
                    event=platform.create_event(msg);event.set_extra('_hosted_contact_subject',item.get('contact_subject'));event.set_extra('_hosted_contact_seen',item.get('contact_seen'));event.set_extra('_hosted_initiative',True);event.set_extra('_hosted_topic_origin',{'kind':'model_selected_topic','actor_id':str(item['self_id']),'recipient_id':str(item.get('contact_subject') or item['subject']),'is_user_statement':False});event.set_extra('_hosted_scheduled_generation',s['generation']);event.set_extra('_hosted_last_user',item['last_user']);event.set_extra('_hosted_previous_output',item['last_output'])
                    item['reserved_at']=now
                    event.set_extra('_hosted_timer_item',item)
                    event.set_extra('_hosted_autonomy_reservation',reservation)
                    if item.get('owner_contact'):event.set_extra('_hosted_owner_contact',item)
                    platform.commit_event(event);m=metrics.setdefault(id,{})
                    if item.get('contact_subject'):m['autonomousGroupAttempts']=m.get('autonomousGroupAttempts',0)+1
                    m['proactiveAttempts']=m.get('proactiveAttempts',0)+1
                except asyncio.CancelledError:raise
                except Exception:
                    # No content/errors from other tenants enter shared logs.
                    m=metrics.setdefault(id,{});m['proactiveSchedulerErrors']=m.get('proactiveSchedulerErrors',0)+1
    return asyncio.create_task(sweep())
