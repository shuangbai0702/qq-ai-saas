"""External tool steps owned by the original Pipeline Work and receipt rows.

This module has no database, scheduler, Journal or target list of its own.
Requests live on the original event only while execution/consumption needs them;
the caller still validates current service permissions before every dispatch.
"""
import hashlib,json,time,uuid

FIELDS=('scope','subject','event','at','stage','status','work_id','fingerprint',
        'authority_epoch','expires_at','execution_started','attempted','confirmed',
        'request','result','lease_owner','lease_until','updated_at')
ACTIVE=('received','waiting','sending')


def initialize(db):
    columns={r[1] for r in db.execute('PRAGMA table_info(pipeline_events)')}
    for name,kind in (('request','TEXT'),('lease_owner','TEXT'),('lease_until','REAL')):
        if name not in columns:db.execute('ALTER TABLE pipeline_events ADD COLUMN '+name+' '+kind)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS pipeline_tool_event ON pipeline_events(event) WHERE source='tool'")


def key(bot,work,step):
    return str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(['fishbot-tool',bot,work,step],separators=(',',':'))))


def row(db,id):
    value=db.execute('SELECT '+','.join(FIELDS)+" FROM pipeline_events WHERE source='tool' AND event=?",(id,)).fetchone()
    if value is None:return None
    result=dict(zip(FIELDS,value))
    result['request']=json.loads(result['request']) if result['request'] else None
    result['result']=json.loads(result['result']) if result['result'] else None
    return result


def rows(db):
    # All live/uncertain owners remain visible regardless of historical volume.
    # Do not deserialize the full terminal tool history on every executor tick.
    return [row(db,x[0]) for x in db.execute("SELECT event FROM pipeline_events WHERE source='tool' AND (status IN ('received','waiting','sending','unknown') OR json_extract(request,'$.completion.state') IN ('pending','claimed','unknown')) ORDER BY at")]


def view(value):
    if not value or not value['request']:return None
    request=value['request'];completion=request.get('completion',{})
    return {**request,'id':value['event'],'work_id':value['work_id'],
            'origin_scope':value['scope'],'sender_id':request.get('sender_id'),
            'state':{'received':'queued','waiting':'claimed','sending':'dispatching','completed':'finished'}.get(value['status'],value['status']),
            'lease_owner':value['lease_owner'],'lease_until':iso(value['lease_until']),
            'deadline_at':iso(value['expires_at']),'created_at':iso(value['at']),
            'updated_at':iso(value['updated_at'] or value['at']),'result':value['result'],
            'completion_state':completion.get('state','none'),
            'completion_owner':completion.get('owner'),'completion_until':iso(completion.get('until'))}


def iso(value):
    if value is None:return None
    from datetime import datetime,timezone
    return datetime.fromtimestamp(value,timezone.utc).isoformat()


def save_request(db,value):
    db.execute("UPDATE pipeline_events SET request=?,updated_at=? WHERE source='tool' AND event=?",
               (json.dumps(value['request'],ensure_ascii=False,separators=(',',':')),time.time(),value['event']))


def same_lease(value,owner):
    return bool(value and value['request'] and value['status']=='waiting' and
                value['lease_owner']==owner and (value['lease_until'] or 0)>time.time() and
                value['expires_at']>time.time() and not value['request'].get('cancel_requested'))


def parent(db,settings,record):
    continuation=record.get('continuation',{})
    if continuation.get('sourceActionId'):
        original=row(db,continuation['sourceActionId']);job=view(original)
        if not job:raise ValueError('tool_parent_missing')
        c=original['request'].get('completion',{})
        if not (job['operation']=='analyze' and job['authority']==record['authority']=='foreground' and
                original['status']=='completed' and c.get('state')=='claimed' and
                c.get('owner')==continuation.get('sourceOwner') and c.get('until',0)>time.time() and
                original['expires_at']>time.time() and not job.get('cancel_requested') and
                all(job.get(k)==record.get(k) for k in ('work_id','sender_id','origin_event','generation','config_revision','settings_revision','account_revision','account_uid')) and
                original['scope']==record.get('origin_scope')):raise ValueError('tool_continuation_stale')
        return original['scope']
    if record['authority']=='autonomous':
        candidate=db.execute("SELECT scope,authority_epoch,status,expires_at FROM pipeline_events WHERE source='autonomous' AND event=?",(record['work_id'],)).fetchone()
        if not candidate or candidate[1]!=record.get('mode_epoch') or candidate[2] not in ('received','sending') or candidate[3]<=time.time():raise ValueError('tool_parent_stale')
        return candidate[0]
    scope,event,sender=record.get('origin_scope'),record.get('origin_event'),record.get('sender_id')
    candidate=db.execute("SELECT status FROM pipeline_events WHERE scope=? AND subject=? AND event=? AND source IN ('message','deferred_group','owner_contact','group_participation')",(scope,sender,event)).fetchone()
    expected='foreground:'+hashlib.sha256(json.dumps([settings['botId'],scope,sender,event],ensure_ascii=False).encode()).hexdigest()
    if record['work_id']!=expected or not candidate or candidate[0] not in ('received','sending'):raise ValueError('tool_parent_stale')
    return scope


def validate_parent(db,settings,record):
    """Read-only validation of the original Work; creates no execution record."""
    for field,expected in (('bot_id',settings['botId']),('tenant_id',settings['tenantId']),('generation',settings['generation']),('config_revision',settings['revision'])):
        if record.get(field)!=expected:raise ValueError('tool_identity_'+field)
    if record.get('authority') not in ('foreground','autonomous'):raise ValueError('tool_authority')
    return {'scope':parent(db,settings,record)}


def admit(pipeline,settings,record):
    db=pipeline.db
    for key_,expected in (('bot_id',settings['botId']),('tenant_id',settings['tenantId']),('generation',settings['generation']),('config_revision',settings['revision'])):
        if record.get(key_)!=expected:raise ValueError('tool_identity_'+key_)
    if record.get('authority') not in ('foreground','autonomous'):raise ValueError('tool_authority')
    if not isinstance(record.get('step_id'),str) or not record['step_id'] or not isinstance(record.get('args_digest'),str):raise ValueError('tool_step_identity')
    expires=record['deadlineAt']/1000
    if not time.time()+.1<expires<=time.time()+601:raise ValueError('tool_deadline')
    id=key(settings['botId'],record['work_id'],record['step_id'])
    with db:
        old=row(db,id)
        if old:
            if not old['request']:raise ValueError('tool_canceled')
            if any(old['request'].get(k)!=record.get(k) for k in ('args_digest','authority','sender_id','mode_epoch','generation','config_revision','settings_revision','account_uid','account_revision','origin_event')):raise ValueError('tool_identity_conflict')
            if record['authority']=='foreground' and old['scope']!=record.get('origin_scope'):raise ValueError('tool_origin_conflict')
            if record.get('continuation',{}).get('sourceActionId'):parent(db,settings,record)
            return view(old)
        scope=parent(db,settings,record)
        if record['operation']=='analyze':
            for existing in rows(db):
                request=existing['request']
                if request and existing['work_id']==record['work_id'] and existing['scope']==scope and existing['status'] in (*ACTIVE,'unknown') and not request.get('cancel_requested') and all(request.get(k)==record.get(k) for k in ('operation','args_digest','authority','sender_id','mode_epoch','generation','config_revision','settings_revision','account_uid','account_revision')):return view(existing)
        # A different caller step cannot repeat an unresolved external write.
        # These are original minimal dispatch receipts, not a second history.
        target=json.dumps([record.get('account_uid'),record['args_digest']],separators=(',',':'))
        if record.get('write') and db.execute("SELECT 1 FROM pipeline_action_effects WHERE method=? AND target=? AND state IN ('dispatching','unknown') LIMIT 1",('bilibili_'+record['operation'],target)).fetchone():raise ValueError('tool_write_unknown')
        request={k:v for k,v in record.items() if k not in ('id','deadlineAt','deadline_at','state','result','lease_owner','lease_until','origin_scope')}
        request['cancel_requested']=False
        db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,work_id,fingerprint,authority_epoch,expires_at,execution_started,request,updated_at) VALUES(?,?,?,?,'tool','model_tools','received','',?,?,?,?,0,?,?)",
                   (scope,str(record.get('sender_id') or 'service:'+settings['botId']),id,time.time(),record['work_id'],record['step_id'],record.get('mode_epoch'),expires,json.dumps(request,ensure_ascii=False,separators=(',',':')),time.time()))
        return view(row(db,id))


def settle(db,value,result):
    request=value['request']
    if request['operation']=='analyze' and (value['scope'] or request['authority']=='autonomous'):
        request['completion']={'state':'pending'}
    status='unknown' if result.get('receipt',{}).get('status')=='unknown' else 'canceled' if result.get('execution')=='canceled' else 'completed'
    db.execute("UPDATE pipeline_events SET status=?,result=?,request=?,finished_at=coalesce(finished_at,?),updated_at=? WHERE source='tool' AND event=?",
               (status,json.dumps(result,ensure_ascii=False,separators=(',',':')),json.dumps(request,ensure_ascii=False,separators=(',',':')),time.time(),time.time(),value['event']))


def recover(db):
    now=time.time()
    for value in rows(db):
        request=value['request']
        if not request or value['status'] not in ACTIVE:continue
        expired=value['expires_at']<=now or request.get('cancel_requested')
        lost=value['status'] in ('waiting','sending') and (value['lease_until'] or 0)<=now
        if (lost or expired) and value['execution_started']:
            db.execute("UPDATE pipeline_action_effects SET state='unknown' WHERE action=? AND state='dispatching'",(value['event'],))
            settle(db,value,{'schemaVersion':'fishbot.tool-result.v1','actionId':value['event'],'execution':'failed','receipt':{'status':'unknown'},'error':{'code':'executor_lost','retry':'reconcile','userMessage':'执行器未确认原操作结果，禁止重复提交'},'usage':{**request.get('usage_checkpoint',{}),'costStatus':'unknown'},'versions':{'adapter':'bilibili-web-v1'}})
        elif expired:
            settle(db,value,{'schemaVersion':'fishbot.tool-result.v1','actionId':value['event'],'execution':'canceled','receipt':{'status':'rejected'},'error':{'code':'canceled_before_completion','retry':'never','userMessage':'任务已取消或超过期限'},'usage':{**request.get('usage_checkpoint',{}),'costStatus':'unknown'},'versions':{'adapter':'bilibili-web-v1'}})
        elif lost:db.execute("UPDATE pipeline_events SET status='received',lease_owner=NULL,lease_until=NULL WHERE source='tool' AND event=?",(value['event'],))


def claim(pipeline,owner,exclude_media=False):
    db=pipeline.db
    with db:
        recover(db)
        candidates=rows(db)
        if any(v['status'] in ('waiting','sending') for v in candidates):return None
        candidates=[v for v in candidates if v['status']=='received' and v['request'] and not (exclude_media and v['request']['operation']=='analyze')]
        candidates.sort(key=lambda v:(v['request']['authority']!='foreground',v['at']))
        if not candidates:return None
        value=candidates[0]
        db.execute("UPDATE pipeline_events SET status='waiting',lease_owner=?,lease_until=?,updated_at=? WHERE source='tool' AND event=? AND status='received'",(owner,time.time()+210,time.time(),value['event']))
        return view(row(db,value['event']))


def advance(pipeline,id,owner,operation,data):
    db=pipeline.db
    with db:
        value=row(db,id)
        if operation=='finish':
            if not value or value['lease_owner']!=owner:return False
            result=data['result']
            if value['status'] not in ('waiting','sending','unknown'):return False
            if value['status']=='unknown' and result.get('receipt',{}).get('status')=='unknown':return False
            settle(db,value,result)
            if value['request'].get('write'):
                state=result.get('receipt',{}).get('status','unknown')
                receipt={'account':value['request'].get('account_uid'),'aid':value['request']['args'].get('aid'),**result.get('receipt',{})}
                # Actual object/receipt identity only; no request text/history.
                receipt.pop('meaning',None)
                db.execute("UPDATE pipeline_action_effects SET state=?,receipt=? WHERE action=? AND sequence=0",(state,json.dumps(receipt,separators=(',',':')),id))
            return True
        if not same_lease(value,owner):raise ValueError('tool_lease_stale')
        if operation=='check':return True
        if operation=='renew':
            db.execute("UPDATE pipeline_events SET lease_until=? WHERE source='tool' AND event=?",(time.time()+210,id));return True
        if operation=='checkpoint':
            value['request']['usage_checkpoint']=data['usage'];save_request(db,value)
            # Persist possible model dispatch BEFORE budget/network awaits.
            db.execute("UPDATE pipeline_events SET execution_started=1 WHERE source='tool' AND event=?",(id,));return True
        if operation=='dispatch':
            request=value['request'];target=json.dumps([request.get('account_uid'),request['args_digest']],separators=(',',':'))
            db.execute("INSERT INTO pipeline_action_effects(action,sequence,method,target,state,receipt,at) VALUES(?,0,?,?,'dispatching',NULL,?)",(id,'bilibili_'+request['operation'],target,time.time()))
            db.execute("UPDATE pipeline_events SET status='sending',stage='dispatch',execution_started=1,attempted=1,updated_at=? WHERE source='tool' AND event=?",(time.time(),id));return True
        raise ValueError('tool_operation_invalid')


def cancel(pipeline,settings,work,step):
    db=pipeline.db;id=key(settings['botId'],work,step)
    with db:
        value=row(db,id)
        if value and value['request']:
            if value['status'] in ACTIVE:value['request']['cancel_requested']=True;save_request(db,value)
        elif not value:
            # A cancel may overtake queue admission. This content-free original
            # event guard prevents a delayed request from becoming new work.
            db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,work_id,fingerprint,expires_at,updated_at) VALUES('','service',?,?,'tool','model_tools','canceled','canceled_before_admission',?,?,?,?)",(id,time.time(),work,step,time.time()+600,time.time()))
    return {'cancelRequested':True}


def retire_ready(db,value):
    """Use the existing legal batch proof, including a delayed final ACK."""
    r=value['request'];c=r.get('completion',{})
    if c.get('state') not in ('done','stale'):return
    from context_protocol import scoped_event_hash,model_json
    channel=value['scope'] or ('self' if r['authority']=='autonomous' else '')
    event=(c.get('consumerWorkId') or 'bilibili-result:'+value['event']) if c['state']=='done' else r.get('origin_event') or (value['work_id'] if r['authority']=='autonomous' else '')
    if channel and event and db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_semantic_batches'").fetchone():
        for scope,logical in ((channel,event),('bot_shared',model_json([channel,event]))):
            member=scoped_event_hash(scope,logical)
            if db.execute("SELECT 1 FROM memory_semantic_batches b,json_each(b.members) m WHERE b.state='retired' AND b.scope=? AND m.value=? LIMIT 1",(scope,member)).fetchone():retire_batch(db,scope,{member});break


def refresh_completions(db,settings,data):
    """Reconcile original consumers before applying current admission fences."""
    now=time.time()
    for value in rows(db):
        r=value['request']
        if not r or r['operation']!='analyze' or value['status'] in ACTIVE:continue
        c=r.get('completion',{})
        if c.get('state')=='claimed' and c.get('until',0)<=now:
            consumer=c.get('consumerWorkId') or 'bilibili-result:'+value['event']
            executed=db.execute('SELECT status,execution_started,attempted FROM pipeline_events WHERE event=? AND scope=?',(consumer,'self' if r['authority']=='autonomous' else value['scope'])).fetchone()
            if executed and (executed[1] or executed[2]):
                c['state']='done' if executed[0] in ('sent','silent','completed','combined','superseded') else 'unknown'
            else:c['state']='pending'
            save_request(db,value)
        stale=any(r.get(k)!=settings[v] for k,v in (('generation','generation'),('config_revision','revision')))
        stale=stale or ('settingsRevision' in data and r.get('settings_revision')!=data['settingsRevision']) or data.get('enabled') is False
        if r['authority']=='autonomous' and 'autonomyEpoch' in data:
            stale=stale or r.get('mode_epoch')!=data['autonomyEpoch'] or data.get('autonomyEnabled') is False
        if stale and c.get('state')=='pending':
            c['state']='stale';save_request(db,value)
        retire_ready(db,value)


def completion(pipeline,settings,data):
    db=pipeline.db;now=time.time()
    with db:
        recover(db);refresh_completions(db,settings,data)
        for value in rows(db):
            r=value['request']
            if not r or r['operation']!='analyze' or r['authority']!=data.get('authority','foreground') or value['status'] in ACTIVE:continue
            if any(r.get(k)!=settings[v] for k,v in (('generation','generation'),('config_revision','revision'))):continue
            if r['authority']=='autonomous' and r.get('mode_epoch')!=data.get('epoch'):continue
            if r.get('completion',{}).get('state')!='pending':continue
            owner=str(uuid.uuid4())
            r['completion']={'state':'claimed','owner':owner,'until':now+120,**({'consumerWorkId':data['consumerWorkId']} if data.get('consumerWorkId') else {})}
            save_request(db,value)
            return {**view(row(db,value['event'])),'owner':owner}
    return None


def acknowledge(pipeline,id,owner,status,consumer=None):
    if status not in ('done','unknown','pending'):raise ValueError('tool_completion_status')
    with pipeline.db:
        value=row(pipeline.db,id)
        if not value or not value['request']:return {'ok':True,'updated':False}
        c=value['request'].get('completion',{})
        if c.get('state') not in ('claimed','unknown') or c.get('owner')!=owner:return {'ok':True,'updated':False}
        if consumer and c.get('consumerWorkId') not in (None,consumer):raise ValueError('tool_consumer_identity')
        c['state']=status;c.pop('until',None)
        if consumer:c['consumerWorkId']=consumer
        save_request(pipeline.db,value)
        retire_ready(pipeline.db,value)
        return {'ok':True,'updated':True}


def retire_batch(db,scope,members):
    """Retire consumed raw tool material at its exact legal Journal boundary.

    The original minimal Work identity and uncertain/confirmed dispatch fences
    stay authoritative. No transcript, raw request or result mirror survives.
    """
    from context_protocol import scoped_event_hash,model_json
    retired=0
    for (id,) in db.execute("SELECT event FROM pipeline_events WHERE source='tool' AND request IS NOT NULL AND status IN ('completed','canceled')").fetchall():
        value=row(db,id);request=value['request'];completion=request.get('completion',{})
        if request['operation']=='analyze' and completion.get('state')!='stale':
            if completion.get('state')!='done':continue
            consumer=completion.get('consumerWorkId') or 'bilibili-result:'+id
            channel='self' if request['authority']=='autonomous' else value['scope']
        else:
            consumer=request.get('origin_event') or (value['work_id'] if request['authority']=='autonomous' else '')
            channel=value['scope'] or ('self' if request['authority']=='autonomous' else '')
        if not consumer or not channel:continue
        logical=model_json([channel,consumer]) if scope=='bot_shared' else consumer
        if scope!='bot_shared' and scope!=channel:continue
        if scoped_event_hash(scope,logical) not in members:continue
        if db.execute("SELECT 1 FROM pipeline_action_effects WHERE action=? AND state IN ('dispatching','unknown')",(id,)).fetchone():continue
        usage=value.get('result',{}).get('usage',{})
        # Only a minimal accounting receipt may outlive raw media material.
        # PostgreSQL's original cycle budget remains the accounting authority.
        accounting=None
        if request['authority']=='autonomous' and request['operation']=='analyze' and value['lease_owner']:
            accounting=json.dumps({'budgetOwner':value['lease_owner'],'usage':{
                'providerUsage':{'total_tokens':usage.get('providerUsage',{}).get('total_tokens')} if isinstance(usage.get('providerUsage'),dict) else None,
                'modelRequestStatus':usage.get('modelRequestStatus')}},separators=(',',':'))
        db.execute("UPDATE pipeline_events SET request=NULL,result=?,lease_owner=NULL,lease_until=NULL WHERE source='tool' AND event=?",(accounting,id))
        retired+=1
    return retired


def handle(pipeline,settings,operation,data):
    if operation=='validate_parent':return validate_parent(pipeline.db,settings,data['record'])
    if operation=='admit':return admit(pipeline,settings,data['record'])
    if operation=='get':return view(row(pipeline.db,data['id'] if data.get('id') else key(settings['botId'],data['workId'],data['stepId'])))
    if operation=='budget_receipt':
        value=row(pipeline.db,data['id'])
        if not value:return None
        result=value.get('result') or {}
        dispatched=pipeline.db.execute('SELECT state,receipt FROM pipeline_action_effects WHERE action=? AND sequence=0',(data['id'],)).fetchone()
        return {'owner':value['lease_owner'] or result.get('budgetOwner'),'terminal':value['status'] not in ACTIVE,
                'usage':result.get('usage',{}),'receipt':json.loads(dispatched[1]) if dispatched and dispatched[1] else result.get('receipt',{}),
                'dispatched':bool(dispatched)}
    if operation=='claim':return claim(pipeline,data['owner'],data.get('excludeMedia',False))
    if operation=='advance':
        if data['phase']!='finish':
            current=row(pipeline.db,data['id']);request=current['request'] if current else {}
            if not request or request.get('generation')!=settings['generation'] or request.get('config_revision')!=settings['revision']:raise ValueError('tool_member_stale')
        return {'accepted':advance(pipeline,data['id'],data['owner'],data['phase'],data)}
    if operation=='cancel':return cancel(pipeline,settings,data['workId'],data['stepId'])
    if operation=='completion':return completion(pipeline,settings,data)
    if operation=='ack':return acknowledge(pipeline,data['id'],data['owner'],data['status'],data.get('consumerWorkId'))
    if operation=='pending':
        with pipeline.db:
            recover(pipeline.db);refresh_completions(pipeline.db,settings,data)
        pending=[]
        for v in rows(pipeline.db):
            r=v['request']
            if not r or data.get('workId') and v['work_id']!=data['workId']:continue
            if v['status'] not in ACTIVE and r.get('completion',{}).get('state') not in ('pending','claimed'):continue
            item=view(v)
            pending.append({k:item.get(k) for k in ('id','work_id','operation','authority','mode_epoch','state','completion_state','generation','config_revision','settings_revision')})
        return {'pending':pending}
    if operation=='effects':
        effects=[]
        for id,method,state,receipt in pipeline.db.execute("SELECT e.event,f.method,f.state,f.receipt FROM pipeline_events e JOIN pipeline_action_effects f ON f.action=e.event WHERE e.source='tool' AND e.work_id=? ORDER BY f.at",(data['workId'],)):
            parsed=json.loads(receipt) if receipt else {'status':'unknown'}
            effects.append({'id':id,'logicalActionId':'bilibili:'+id,'action':method,'status':state if state in ('confirmed','rejected') else 'unknown','receipt':parsed,'source':'bilibili'})
        return {'effects':effects}
    if operation=='receipt':
        found=False
        for receipt, in pipeline.db.execute("SELECT receipt FROM pipeline_action_effects WHERE method=? AND state='confirmed' AND receipt IS NOT NULL",('bilibili_'+data['operation'],)):
            r=json.loads(receipt)
            if r.get('account')==data.get('accountUid') and r.get('platformObjectId')==data['objectId'] and (data.get('aid') is None or r.get('aid')==data['aid']):found=True;break
        return {'confirmed':found}
    if operation=='list':
        limit=max(1,min(48,int(data.get('limit',20))));result=[];used=0
        ids=pipeline.db.execute("SELECT event FROM pipeline_events WHERE source='tool' AND request IS NOT NULL ORDER BY at DESC LIMIT ?",(limit,)).fetchall()
        for (id,) in ids:
            item=view(row(pipeline.db,id))
            if data.get('summary') and item.get('result',{}):
                item['result']=dict(item['result']);body=item['result'].get('data')
                if isinstance(body,dict):item['result']['data']={k:(v[:700] if isinstance(v,str) else v) for k,v in body.items() if k in ('bvid','part','modality','clip','analysis','title','audio')}
            size=len(json.dumps(item,ensure_ascii=False).encode())
            if used+size>110000:break
            result.append(item);used+=size
        return {'jobs':result}
    raise ValueError('tool_operation_invalid')
