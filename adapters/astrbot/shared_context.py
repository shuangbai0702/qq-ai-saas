"""Bot-wide chat sequencing. Delivery scopes and permissions stay unchanged.

The existing Journal owns wire history; this module only selects its logical
key, serializes writers, and prepares new, never-submitted inputs.
"""
import asyncio,copy,json,re,time,hashlib
from context_protocol import SOURCE,source,model_json,event_message

JOURNAL_SCOPE='bot_shared'


def group_whitelist(cfg,group_limit=2):
    values=(cfg.get('behavior') or cfg).get('groupParticipationGroups',[])
    if not isinstance(values,list) or any(not isinstance(x,str) or not re.fullmatch(r'\d{4,20}',x) for x in values):
        raise ValueError('shared_context_invalid_group_whitelist')
    groups=tuple(dict.fromkeys(values))
    if type(group_limit) is not int or group_limit not in (2,4):raise ValueError('shared_context_invalid_group_limit')
    if len(groups)>group_limit:raise ValueError('shared_context_at_most_two_groups' if group_limit==2 else 'shared_context_group_limit_exceeded')
    return groups


def included(scope,cfg,group_limit=2):
    groups=group_whitelist(cfg,group_limit)
    return bool(re.fullmatch(r'private:\d{5,20}',scope) or
                scope.startswith('group:') and scope[6:] in groups)


def event_key(origin):
    """Message IDs are only unique in their protocol source, never bot-wide."""
    return model_json([origin['scope'],str(origin['event_id'])])


class Writer:
    """One bot's model/ToolLoop writer, not a network-send mutex.

    Ownership is a task, not a Work ID: two invocations of the same Work must
    not both enter. Continuation calls from that same task are reentrant.
    """
    def __init__(self):
        self.lock=asyncio.Lock();self.owner=None

    async def acquire(self):
        task=asyncio.current_task()
        if self.owner is task:return
        await self.lock.acquire()
        self.owner=task

    def release(self):
        if self.owner is not asyncio.current_task():return False
        self.owner=None;self.lock.release();return True


def active(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='fishbot_shared_context'").fetchone():return False
    return db.execute("SELECT 1 FROM fishbot_shared_context WHERE id=1 AND state='active'").fetchone() is not None


def initialize(db):
    # An ingress staging queue, NOT a second submitted-message history. Rows
    # leave only after the authoritative Journal durably contains them.
    db.executescript('''CREATE TABLE IF NOT EXISTS fishbot_shared_inputs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,event TEXT NOT NULL,
      message TEXT NOT NULL,created REAL NOT NULL,UNIQUE(scope,event));''')


def activate_authorized(db,cfg,expected,authorization,release,group_limit=2):
    """Offline one-time operator cutover, only with the worker held and backup.

    `expected` is the complete participating Journal scope->SHA inventory from
    the operator's preflight. No epoch/System/checkpoint is manufactured. Old
    evidence and tasks stay intact; only the explicitly ended Journals leave
    the active table, byte-for-byte in the immutable operator archive.
    """
    group_whitelist(cfg,group_limit)
    if not isinstance(authorization,str) or not 8<=len(authorization)<=100 or not re.fullmatch(r'[a-f0-9]{40}',release):
        raise ValueError('shared_context_explicit_authorization_required')
    if db.in_transaction:raise ValueError('shared_context_operator_requires_idle_database')
    initialize(db)
    db.execute('BEGIN IMMEDIATE')
    try:
        if active(db):raise ValueError('shared_context_already_active')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows=[]
        if 'fishbot_readonly_journals' in tables:
            for row in db.execute('SELECT scope,version,state,payload,sha,updated FROM fishbot_readonly_journals'):
                channel=json.loads(row[0])
                if isinstance(channel,str) and included(channel,cfg,group_limit):rows.append(row)
        actual={json.loads(r[0]):r[4] for r in rows}
        if actual!=expected:raise ValueError('shared_context_journal_inventory_changed')
        if any(not r[3] or hashlib.sha256(r[3].encode()).hexdigest()!=r[4] for r in rows):
            raise ValueError('shared_context_journal_checksum_invalid')
        db.execute('''CREATE TABLE IF NOT EXISTS fishbot_operator_session_archives(
          authorization TEXT PRIMARY KEY,scope TEXT NOT NULL,release TEXT NOT NULL,archive TEXT NOT NULL,
          sha TEXT NOT NULL,created REAL NOT NULL,new_session_state TEXT NOT NULL CHECK(new_session_state IN ('available','consumed')))''')
        db.execute("CREATE TRIGGER IF NOT EXISTS operator_session_archive_immutable BEFORE UPDATE OF authorization,scope,release,archive,sha,created ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'operator archive is immutable'); END")
        db.execute("CREATE TRIGGER IF NOT EXISTS operator_session_archive_no_delete BEFORE DELETE ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'operator archive is immutable'); END")
        for row in rows:
            # No replay/reset of memory, execution effects, permissions or
            # consumed licenses. The original evidence remains queryable.
            encoded=model_json({'journal':dict(zip(('scope','version','state','payload','sha','updated'),row)),
                                'meaning':'operator-ended old logical session; not a 35-round checkpoint'})
            key=authorization+':'+hashlib.sha256(row[0].encode()).hexdigest()[:16]
            db.execute("INSERT INTO fishbot_operator_session_archives VALUES(?,?,?,?,?,?,'consumed')",(key,row[0],release,encoded,hashlib.sha256(encoded.encode()).hexdigest(),time.time()))
            db.execute('DELETE FROM fishbot_readonly_journals WHERE scope=? AND sha=?',(row[0],row[4]))
        db.execute('''CREATE TABLE IF NOT EXISTS fishbot_shared_context(
          id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL,authorization TEXT NOT NULL,created REAL NOT NULL,
          group_limit INTEGER NOT NULL CHECK(group_limit IN (2,4)))''')
        db.execute("INSERT INTO fishbot_shared_context VALUES(1,'active',?,?,?)",(authorization,time.time(),group_limit))
        db.commit()
    except BaseException:
        db.rollback();raise
    return {'archived':len(rows),'logicalJournal':JOURNAL_SCOPE}


def observe(db,event,cfg,group_limit=2):
    from astrbot.core.message.components import Image,Plain,At,Reply,Face,Record
    scope='group:'+str(event.get_group_id()) if event.get_group_id() else 'private:'+str(event.get_sender_id())
    if not included(scope,cfg,group_limit):return False
    identifier=str(getattr(event.message_obj,'message_id',''))
    if not identifier:raise ValueError('shared_context_event_id_missing')
    sender=str(event.get_sender_id());at=float(getattr(event.message_obj,'timestamp',0) or time.time())
    origin={'scope':scope,'sender_id':sender,'event_id':identifier,'at':at}
    parts=list(event.get_messages());mentions=[str(c.qq) for c in parts if isinstance(c,At)]
    if mentions:origin['mentions']=list(dict.fromkeys(mentions))
    quoted=next((c for c in parts if isinstance(c,Reply)),None)
    if quoted is not None:origin['reply_to']={'sender_id':str(quoted.sender_id),'event_id':str(getattr(quoted,'id','')),'text':str(getattr(quoted,'message_str','') or '')}
    # Background events may enter before their own SDK preprocessing. Preserve
    # observable component kinds rather than silently turning them into empty
    # messages or pretending an untranscribed recording has been understood.
    body=''.join(c.text if isinstance(c,Plain) else
                 '[QQ表情 id='+str(c.id)+']' if isinstance(c,Face) else
                 '[语音消息，尚未转写]' if isinstance(c,Record) else '' for c in parts)
    owner=str((cfg.get('behavior') or cfg).get('ownerUin') or '')
    if sender==owner and owner:origin['attention']='[important]'
    content=[{'type':'text','text':body}]
    for c in parts:
        if isinstance(c,Image):
            location=getattr(c,'url',None) or getattr(c,'file',None)
            if isinstance(location,str) and location:
                if location.startswith('base64://'):location='data:image/jpeg;base64,'+location[9:]
                content.append({'type':'image_url','image_url':{'url':location}})
    message=event_message(origin,content)
    raw=model_json(message)
    from message_provenance import bind_model_input
    signature=bind_model_input(db,JOURNAL_SCOPE,scope,identifier,sender,message)
    event.set_extra('_hosted_shared_input_digest',signature)
    # A redelivery cannot replace the first protocol-bound author or body.
    db.execute('INSERT OR IGNORE INTO fishbot_shared_inputs(scope,event,message,created) VALUES(?,?,?,?)',(scope,identifier,raw,time.time()))
    db.commit();return True


def queued(db,cfg,journal,group_limit=2,current=None):
    known={(o['scope'],str(o['event_id'])) for m in (journal.last.base if journal.last else []) if (o:=source(m))}
    rows=db.execute('SELECT scope,event,message FROM fishbot_shared_inputs ORDER BY id').fetchall()
    result=[]
    for scope,event,raw in rows:
        if included(scope,cfg,group_limit) and (scope,event) not in known:
            message=json.loads(raw)
            if current and (scope,event)==current[:2]:
                # Prefer SDK-rendered quotes/voice/media for the current NEW
                # event. A previously committed event never enters this branch.
                native=copy.deepcopy(current[2]);a=source(native);b=source(message)
                if not a or not b or any(str(a.get(k))!=str(b.get(k)) for k in ('scope','event_id','sender_id')):raise ValueError('shared_context_current_author_changed')
                content=native['content'];parts=[{'type':'text','text':content}] if isinstance(content,str) else content
                line,sep,body=parts[0]['text'].partition('\n');a['at']=b.get('at')
                parts[0]['text']=SOURCE+model_json(a)+'\n'+body
                # Only this never-submitted event may receive SDK enrichment.
                # Freeze the queue's trusted time/identity and a single shape.
                parts[0]={**parts[0],'text':body}
                message={**native,**event_message(a,parts)}
            result.append(message)
    return result


def settle_inputs(db,journal):
    known=[(o['scope'],str(o['event_id'])) for m in journal.last.base if (o:=source(m))] if journal.last else []
    db.executemany('DELETE FROM fishbot_shared_inputs WHERE scope=? AND event=?',known)
    db.commit()


def recent(db,limit=12,budget=3000,scope=None):
    """Bounded auxiliary judgment view of the ONE committed Journal."""
    from journal_storage import read_snapshot
    snapshot=read_snapshot(db,model_json(JOURNAL_SCOPE))
    if snapshot is None:return []
    messages=snapshot.get('base',[]);result=[]
    for message in messages:
        origin=source(message)
        if origin:
            if scope is not None and origin['scope']!=scope:continue
            content=message.get('content');text=content if isinstance(content,str) else '\n'.join(p.get('text','') for p in content if p.get('type')=='text')
            result.append({'source_scope':origin['scope'],'sender_id':origin['sender_id'],'at':origin.get('at'),
                           'source_event':origin['event_id'],'text':text.split('\n',1)[-1][:600],
                           **({'attention':'[important]'} if origin.get('attention')=='[important]' else {})})
        elif scope is None and message.get('role')=='assistant' and isinstance(message.get('content'),str) and message['content']:
            result.append({'role':'self','text':message['content'][:600],'delivery':'model_output_not_send_receipt'})
    result=result[-limit:]
    while result and len(model_json(result))>budget:result.pop(0)
    return result


def delivery_view(db,journal=None,limit=8,budget=3000,exclude_scope=None,text_limit=1200,only_scope=None):
    """Read existing settled protocol evidence, not another message history.

    Terminal assistant messages are model drafts. Freshness may replace/drop
    them, and a network send may remain unknown. Publish those differences in
    the next readonly snapshot; never edit the already committed model draft
    or add an unsourced user turn that coverage could never retire.
    """
    if journal is None:
        # A newly activated bot may build its first Work before the Journal
        # constructor has created persistence. No submitted history means no
        # deliveries; do not suppress other database errors.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fishbot_readonly_journals'").fetchone():return []
        from journal_storage import read_snapshot
        snapshot=read_snapshot(db,model_json(JOURNAL_SCOPE))
        if snapshot is None:return []
        messages=snapshot.get('base',[])
    else:
        if journal.last is None:return []
        messages=journal.last.base
    known={(o['scope'],str(o['event_id'])) for m in messages if (o:=source(m)) and o['scope']!=exclude_scope}
    rows=db.execute('''SELECT p.scope,p.subject,p.event,p.status,p.attempted,p.confirmed,
                      d.reply,d.at,s.data,d.state FROM pipeline_events p
                      LEFT JOIN dialogue_feedback d ON d.scope=p.scope AND d.event=p.event
                      LEFT JOIN memory_message_sources s ON s.scope=p.scope AND s.event=p.event AND s.direction='outgoing'
                      WHERE p.status NOT IN ('received','sending','waiting','duplicate','silent')
                      ORDER BY p.at DESC LIMIT 64''').fetchall()
    result=[];used=0
    has_signals=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='companion_signals'").fetchone())
    for scope,subject,event,status,attempted,confirmed,text,at,raw,feedback_state in rows:
        if (scope,str(event)) not in known or only_scope is not None and scope!=only_scope:continue
        try:receipt=json.loads(raw) if raw else {}
        except (ValueError,TypeError):receipt={}
        if not isinstance(receipt,dict):receipt={}
        ids=receipt.get('message_ids')
        proven=receipt.get('receipt')=='confirmed' and isinstance(ids,list) and bool(ids) and isinstance(text,str) and bool(text)
        item={'source_scope':scope,'source_event':str(event),'recipient_id':subject}
        if proven:
            item.update(delivery='confirmed' if status=='sent' and receipt.get('complete') is True else 'partial',
                        confirmed_text=text[:text_limit],text_complete=len(text)<2000 and len(text)<=text_limit,at=at)
        else:
            # A completed model response or an attempted protocol write does
            # not establish delivery. Never infer success from Work status.
            item['delivery']='unknown' if attempted or confirmed or status in ('sent','partial','unknown') else 'not_sent'
        # Human feedback changes interpretation, never protocol confirmation.
        # Older records have only state; do not invent a reaction or quote.
        if feedback_state in ('engaged','confused','corrected','rejected'):
            item['feedback']={'state':feedback_state}
            signal=db.execute("SELECT quote FROM companion_signals WHERE scope=? AND event=? AND kind='dialogue_feedback'",(scope,event)).fetchone() if has_signals else None
            if signal:
                evidence=json.loads(signal[0])
                if isinstance(evidence,dict) and evidence.get('state')==feedback_state:item['feedback']=evidence
        size=len(model_json(item))
        if used+size>budget:continue
        result.append(item);used+=size
        if len(result)>=limit:break
    return list(reversed(result))


def delivery_context(db,journal):
    return {'deliveries':delivery_view(db,journal),
            'meaning':'历史assistant内容是模型原稿，不等于已发送；deliveries记录原会话最终聊天回复的投递结算，不替代独立工具回执。confirmed_text为协议确认文本，text_complete=false时只是有界摘录；unknown不代表失败或允许重发。feedback是对方后续反应，corrected/rejected不撤销协议投递确认，也不授予重发权限。'}


def refresh_deliveries(messages,db,journal):
    """Refresh only the new SDK readonly packet, before canonical composition.

    Caller holds the bot writer. A preceding send can settle while this Work
    waits for that writer; do not freeze its receipts at ContextBuilder time.
    Old Journal wire is never passed here or edited by this function.
    """
    from context_transport import READONLY_CURRENT
    result=[]
    for message in messages:
        content=message.get('content')
        if message.get('role')=='user' and isinstance(content,str) and content.startswith(READONLY_CURRENT+'\n'):
            data=json.loads(content.split('\n',1)[1])
            if isinstance(data,dict):
                dialogue=data.get('dialogue',{})
                if isinstance(dialogue,dict):
                    data['dialogue']={**dialogue,**delivery_context(db,journal)}
                    message={**message,'content':READONLY_CURRENT+'\n'+model_json(data)}
        result.append(message)
    return result


def native_tail(messages,scope,event,service=False):
    """SDK supplies the current event/tool continuation, never shared history."""
    positions=[i for i,m in enumerate(messages) if (o:=source(m)) and o['scope']==scope and str(o['event_id'])==str(event)]
    if not positions and service:
        from context_transport import READONLY_CURRENT,EVENT_CONTEXT_CURRENT
        positions=[i for i,m in enumerate(messages) if m.get('role')=='user' and not (isinstance(m.get('content'),str) and m['content'].startswith((READONLY_CURRENT+'\n',EVENT_CONTEXT_CURRENT+'\n')))]
    if not positions:raise ValueError('shared_context_current_event_missing')
    tail=messages[positions[-1]:]
    if any(m.get('role') in ('system','developer') for m in tail):raise ValueError('shared_context_system_in_tail')
    from context_transport import READONLY_CURRENT,EVENT_CONTEXT_CURRENT
    tail=[m for m in tail if not (m.get('role')=='user' and isinstance(m.get('content'),str)
                                and m['content'].startswith((READONLY_CURRENT+'\n',EVENT_CONTEXT_CURRENT+'\n')))]
    return copy.deepcopy(tail)


def current_view(messages,scope,event,service=False):
    """SDK contributes NEW input and platform packets, never old media copies.

    The canonical submitted prefix is appended separately by compose(). Do not
    decode/recompress the SDK's competing copy of that same historical prefix.
    """
    from context_transport import READONLY_CURRENT,EVENT_CONTEXT_CURRENT
    head=[m for m in messages if m.get('role') in ('system','developer') or
          m.get('role')=='user' and isinstance(m.get('content'),str) and
          m['content'].startswith((READONLY_CURRENT+'\n',EVENT_CONTEXT_CURRENT+'\n'))]
    return copy.deepcopy(head)+native_tail(messages,scope,event,service)


def compose(journal,messages,scope,event,inputs=None,previous_tools=None,service=False,self_id='',input_digest=None):
    """Keep the exact canonical native prefix; append this event's new tail.

    Never overlap-match another SDK conversation or copy its old history.
    Platform readonly/authority packets are handled by ReadonlyJournal itself.
    """
    from readonly_journal import system_head
    from context_transport import READONLY_CURRENT,EVENT_CONTEXT_CURRENT
    packets=[copy.deepcopy(m) for m in messages if m.get('role')=='user' and isinstance(m.get('content'),str)
             and m['content'].startswith((READONLY_CURRENT+'\n',EVENT_CONTEXT_CURRENT+'\n'))]
    native=native_tail(messages,scope,event,service)
    if not service:
        committed=next((m for m in (journal.last.base if journal.last else []) if (o:=source(m)) and o['scope']==scope and str(o['event_id'])==str(event)),None)
        if committed is not None:
            old_source=source(committed);live_source=source(native[0])
            if not live_source or str(old_source['sender_id'])!=str(live_source['sender_id']):raise ValueError('shared_context_current_author_changed')
            if input_digest is not None:
                from message_provenance import get
                proof=get(journal.db,JOURNAL_SCOPE,event_key(old_source),'model')
                if proof.get('input_digest')!=input_digest:raise ValueError('shared_context_protocol_input_changed')
    first_call=previous_tools is None
    tools=native[1:];previous_tools=previous_tools or []
    if tools[:len(previous_tools)]!=previous_tools:raise ValueError('shared_context_tool_prefix_changed')
    service_input=[]
    if service and first_call:
        if not re.fullmatch(r'\d{5,20}',str(self_id)):raise ValueError('shared_context_service_identity_missing')
        origin={'scope':scope,'sender_id':str(self_id),'event_id':str(event),'at':time.time(),'kind':'platform_service_not_user'}
        content=copy.deepcopy(native[0].get('content'))
        header=SOURCE+model_json(origin)+'\n[平台内部触发，不是用户发言或主人新指令]\n'
        if isinstance(content,str):content=header+content
        elif isinstance(content,list):content=[{'type':'text','text':header},*content]
        else:raise ValueError('shared_context_service_input_shape')
        service_input=[{'role':'user','content':content}]
    tail=copy.deepcopy(inputs or [])+service_input+tools[len(previous_tools):]
    old=journal.last.base if journal.last else messages[:system_head(messages)]
    identities={(o['scope'],str(o['event_id'])) for m in [*old,*tail] if (o:=source(m))}
    if not service and (scope,str(event)) not in identities:raise ValueError('shared_context_unobserved_event')
    return copy.deepcopy(old)+packets+tail,copy.deepcopy(tools)
