"""Independent owner-contact checks fenced by conversation and runtime identity."""
from model_cache import auxiliary_scope,adopted,discarded
import asyncio,time
from initiative import quiet,paused
import companion_state
import autonomy
from context_transport import decision_request
from bot_pipeline import decision_memory
from model_cache import usage_evidence
from datetime import datetime,timezone,timedelta


class Planner:
    def __init__(self,db):
        self.db=db;self.closed=False;self.task=None
        db.executescript('''CREATE TABLE IF NOT EXISTS companion_plans(scope TEXT PRIMARY KEY,anchor REAL,due REAL,topic TEXT,allowed INTEGER);
        CREATE TABLE IF NOT EXISTS companion_plan_members(scope TEXT PRIMARY KEY,anchor REAL,subject TEXT,seen REAL);
        CREATE TABLE IF NOT EXISTS companion_plan_budget(day INTEGER PRIMARY KEY,count INTEGER);''')
        db.execute("UPDATE autonomy_owner_checks SET status='interrupted' WHERE status='pending'");db.commit()
    def plan(self,scope,anchor):
        if self.closed:return None
        row=self.db.execute('SELECT due,topic,allowed FROM companion_plans WHERE scope=? AND anchor=?',(scope,anchor)).fetchone()
        return {'due':row[0],'topic':row[1],'allowed':bool(row[2])} if row else None
    def maybe_start(self,cfg,provider,current,event_var,metrics,now=None,lease=None):
        b=cfg.get('behavior') or {};now=time.time() if now is None else now
        if self.closed or not current() or b.get('enabled',True) is False or not (cfg.get('proactive') or b.get('ownerContactEnabled',autonomy.BEHAVIOR_DEFAULTS['ownerContactEnabled'])) or not provider:return
        # A broken credential must not turn every planning sweep into another
        # doomed request; per-bot backoff resumes on the next config apply (C).
        # Quiet hours block actions, not the local clock established on enable.
        autonomy.owner_state(self.db,cfg,now)
        if quiet(cfg,now) or self.task and not self.task.done():return
        if b.get('dailyProactiveLimit',10)<=0 or b.get('hourlyInterjectionLimit',20)<=0:return
        owner=autonomy.owner_check_candidate(self.db,cfg,now)
        if owner and not autonomy.auth_paused(lease,'owner_contact'):
            self.start_owner(cfg,provider,current,event_var,metrics,owner,now,lease=lease)
            return
        # Genuine autonomous activation is owned by the main AI runtime.
        # The retired contact planner had no delivery consumer and could write
        # learned motives outside compression. This sweep only checks the owner.
    def start_owner(self,cfg,provider,current,event_var,metrics,candidate,now,lease=None):
        (owner,scope,contact,state),day=candidate
        b=cfg.get('behavior') or {}
        with self.db:
            self.db.execute('UPDATE autonomy_owner SET checked=?,allowed=0,checked_version=? WHERE owner=?',(now,state[2],owner))
            self.db.execute("INSERT INTO autonomy_owner_checks VALUES(?,?,?,'pending',0)",(owner,now,state[2]))
            self.db.execute('DELETE FROM autonomy_owner_checks WHERE rowid NOT IN (SELECT rowid FROM autonomy_owner_checks ORDER BY rowid DESC LIMIT 128)')
        async def run():
            with auxiliary_scope(provider,'owner_contact',None,scope):
                token=event_var.set(None);limit_token=autonomy.decision_token_limit.set(autonomy.OWNER_DECISION_OUTPUT_TOKENS)
                started=time.monotonic()
                def result_record(status):
                    if self.closed or not current():return
                    self.db.execute('UPDATE autonomy_owner_checks SET status=?,elapsed_ms=? WHERE owner=? AND checked=?',(status,round((time.monotonic()-started)*1000),owner,now));self.db.commit()
                try:
                    if self.closed or not current():discarded('runtime_replaced');return
                    latest=autonomy.owner_state(self.db,cfg,now)
                    if not latest or latest[0]!=owner or latest[2][2:5]!=contact[2:5] or latest[3][2]!=state[2]:
                        discarded('conversation_changed');result_record('conversation_changed');return
                    if paused(self.db,scope,now) or quiet(cfg,now):
                        discarded('contact_paused');result_record('contact_paused');return
                    if not autonomy.has_capacity(self.db,cfg,now):
                        discarded('send_capacity_exhausted');result_record('send_capacity_exhausted');return
                    with self.db:
                        self.db.execute('INSERT INTO autonomy_owner_budget VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1',(day,))
                        self.db.execute('DELETE FROM autonomy_owner_budget WHERE day<?',((datetime.fromtimestamp(now,timezone(timedelta(hours=8)))-timedelta(days=7)).date().isoformat(),))
                    metrics['ownerDecisionRequests']=metrics.get('ownerDecisionRequests',0)+1
                    from memory_scope import pending_messages
                    from decision_events import contact_messages
                    memory=decision_memory(self.db,cfg,scope,owner,now,recent=False)
                    if b.get('memory',True):
                        # This judgment has no native chat contexts. Keep a separate
                        # bounded private window so a busy group cannot displace it.
                        from shared_context import active,recent
                        memory.update({'recent_private':recent(self.db,4,1200,scope=scope,auxiliary=True) if active(self.db) else pending_messages(self.db,scope,owner,now,limit=4,budget=1200,cross_scope=False),'recent_uncompressed':recent(self.db,4,1200,auxiliary=True) if active(self.db) else pending_messages(self.db,scope,owner,now,limit=4,budget=1200,owner=owner)})
                    timing={'last_private_user_at':contact[2],'last_private_bot_at':contact[3] or None,'last_owner_interaction_at':state[1],'owner_idle_seconds':max(0,int(now-state[1])),'private_idle_seconds':max(0,int(now-max(contact[2:4]))),'last_proactive_attempt_at':contact[4] or None}
                    # Background owner checks are not time-sensitive group admission.
                    # Allow the configured chat provider to finish; real conversation
                    # or configuration changes still invalidate the result below.
                    result=await asyncio.wait_for(provider.text_chat(**decision_request({'owner_id':owner,'persona':str(cfg.get('systemPrompt') or ''),'scope':scope,'companion':companion_state.context(self.db,cfg,scope,now),'owner_memory':memory,'conversation_timing':timing,'cooldown_base_seconds':autonomy.cooldown_base(cfg,scope,'owner'),'messages':contact_messages(self.db,scope),'current_time':datetime.fromtimestamp(now,timezone(timedelta(hours=8))).isoformat()},autonomy.OWNER_SYSTEM+autonomy.COOLDOWN_SYSTEM,reference_first=True),func_tool=None,max_tokens=autonomy.OWNER_DECISION_OUTPUT_TOKENS,request_max_retries=1),60)
                    if self.closed or not current():discarded('runtime_replaced');return
                    metrics['lastOwnerUsage']={'at':time.time(),**usage_evidence(result)}
                    latest=autonomy.owner_state(self.db,cfg,time.time())
                    if not latest or latest[0]!=owner or latest[2][2:5]!=contact[2:5] or paused(self.db,scope,time.time()) or quiet(cfg,time.time()):
                        metrics['lastOwnerDecision']={'at':now,'contact':False,'reason':'conversation_changed'}
                        discarded('conversation_changed');result_record('conversation_changed');return
                    if metrics['lastOwnerUsage'].get('finishReason')=='length':raise ValueError('owner_output_truncated')
                    data=autonomy.parse_object(result.completion_text)
                    autonomy.model_cooldown(data)
                    if type(data.get('contact')) is not bool or not isinstance(data.get('topic'),str) or 'reason' in data and (not isinstance(data['reason'],str) or len(data['reason'])>240):raise ValueError('Invalid owner decision')
                    topic=data['topic'].strip()[:80];allowed=bool(data['contact'] and topic)
                    with self.db:
                        changed=self.db.execute('UPDATE autonomy_owner SET allowed=?,topic=? WHERE owner=? AND checked=? AND version=?',(int(allowed),topic,owner,now,state[2])).rowcount
                        cooldown=autonomy.record_decision(self.db,cfg,scope,'owner:'+str(now),data,allowed,now,'owner') if changed else {}
                    metrics['lastOwnerDecision']={'at':now,'contact':allowed if changed else False,'reason':('model_contact' if allowed else 'model_silent') if changed else 'conversation_changed',**cooldown}
                    result_record(metrics['lastOwnerDecision']['reason'])
                    if changed:adopted('owner_decision_applied')
                    else:discarded('conversation_changed')
                except asyncio.CancelledError:raise
                except Exception as error:
                    discarded(type(error).__name__)
                    if autonomy.failure_class(error)=='auth':autonomy.note_auth_failure(lease,'owner_contact')
                    if not self.closed and current():
                        metrics['ownerDecisionFailures']=metrics.get('ownerDecisionFailures',0)+1
                        metrics['lastOwnerDecision']={'at':now,'contact':None,'status':'failed','reason':str(error) if str(error)=='owner_output_truncated' else type(error).__name__,'failureClass':autonomy.failure_class(error)}
                        result_record('timeout' if isinstance(error,TimeoutError) else 'error:'+type(error).__name__)
                finally:autonomy.decision_token_limit.reset(limit_token);event_var.reset(token)
        self.task=asyncio.create_task(run())
    def close(self):
        self.closed=True
        if self.task:self.task.cancel()
