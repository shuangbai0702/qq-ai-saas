"""R5: one bounded business projection for the bot's real commitments.

Summary claims stay the historical evidence layer; this table is the current
state of agreements that need managing (a 02:25-class "don't reply when the
owner @s you in the group" instruction must reach the group decision as a
current, trusted commitment — not as background data and not 35 rounds late).

Code owns identity, participants, revision CAS, status transitions and the
audience filter. The model owns meaning: whether something is a commitment,
who is bound, and what it says. No keywords, no second scheduler, no new
memory store; the durable task ledger keeps owning actual execution.
"""
import asyncio,hashlib,json,time

STATUSES=('proposed','accepted','fulfilled','canceled','expired','uncertain')
OPEN_STATUSES=('proposed','accepted','uncertain')
MAX_OPEN=32
MAX_TOPIC=120
MAX_DETAIL=200
MAX_AUDIENCES=8
AGREEMENT_TTL=30*86400


def initialize(db):
    # Existence-checked, not connection-cached: id() reuse across collected
    # connections must never skip the table's creation on a fresh database.
    # The DDL branch only runs for a missing table (first use), never inside
    # a caller's open transaction where executescript() would commit it.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_agreements'").fetchone():
        return
    db.executescript('''CREATE TABLE IF NOT EXISTS memory_agreements(
      id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 1,topic TEXT NOT NULL,detail TEXT NOT NULL,
      speaker TEXT NOT NULL,participants TEXT NOT NULL,source_scope TEXT NOT NULL,source_event TEXT NOT NULL,
      audiences TEXT NOT NULL,status TEXT NOT NULL,due TEXT,created REAL NOT NULL,updated REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS memory_agreements_updated ON memory_agreements(updated);''')


def agreement_identity(scope,event,topic):
    return hashlib.sha256('\x00'.join(('agreement',scope,str(event),topic.strip())).encode()).hexdigest()[:24]


def _valid_scope(value):
    return isinstance(value,str) and value and len(value)<=64 and (
        value=='self' or value.startswith(('group:','private:')))


def _participants_ok(values):
    return isinstance(values,list) and 1<=len(values)<=8 and all(
        isinstance(v,str) and (v=='bot' or len(v)<=20 and v.isdigit()) for v in values)


def visible(db,scope,now=None):
    """Rows whose recorded audiences include this scope, bounded, open first.

    Terminal rows stay briefly for reference and then age out; they never
    masquerade as current commitments. Cross-scope rows expose only the
    bounded state (topic/status/due/participants) — never the source chat's
    verbatim text.
    """
    now=time.time() if now is None else now
    result=[]
    for row in db.execute('SELECT id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated FROM memory_agreements ORDER BY updated DESC LIMIT 128'):
        try:audiences=json.loads(row[8])
        except (ValueError,TypeError):continue
        if not isinstance(audiences,list) or scope not in audiences:continue
        if row[9] not in OPEN_STATUSES and now-row[12]>AGREEMENT_TTL:continue
        result.append({'id':row[0],'revision':row[1],'topic':row[2],'status':row[9],
                       'due':row[10] or '','speaker':row[4],'participants':json.loads(row[5]) if row[5] else [],
                       'source_scope':row[6],'source_event':row[7],'updated':row[12]})
        if len(result)>=8:break
    return result


def context_view(db,scope,owner=''):
    """Bounded "当前有效事项" view for a new request. The view is the bot's
    own effective commitment state — honored when it applies to the current
    conversation, and never a source of new permissions or identity."""
    rows=visible(db,scope)
    for row in rows:row.pop('detail',None)
    return rows


def agreements_stamp(db,scope):
    """Content-free revision fingerprint of what this scope currently sees.

    Rides the existing context fence: a mid-Work revision or cancellation
    invalidates the stale wire candidate the same way a summary completion
    does, instead of letting an already-stale reply ship."""
    rows=visible(db,scope)
    return hashlib.sha256(json.dumps([[r['id'],r['revision'],r['status']] for r in rows],
                                     ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:16]


def _allowed_audiences(db,scope,speaker,participants,cfg,proposed):
    """Program-side audience decision. The model proposes; this filter
    authorizes: the current scope, private chats of real participants, and —
    only for the configured owner — groups the bot actually serves."""
    allowed={scope} if _valid_scope(scope) else set()
    for person in participants or []:
        if person!='bot':allowed.add('private:'+person)
    owner=str((cfg or {}).get('ownerUin') or '')
    if speaker==owner and owner:
        for group in (cfg or {}).get('groups') or []:
            if isinstance(group,str) and group.isdigit():allowed.add('group:'+group)
    result=[value for value in dict.fromkeys(proposed or []) if value in allowed][:_MAX_AUD]
    return result or ([scope] if _valid_scope(scope) else [])

_MAX_AUD=MAX_AUDIENCES


def apply_update(db,action,topic,speaker,participants,source_scope,source_event,audiences=None,
                 detail='',due='',cfg=None,now=None):
    """One CAS'd state write. Returns (row dict, error string). No keyword
    guessing: the caller's model already judged this a real commitment."""
    now=time.time() if now is None else now
    initialize(db)
    if action not in ('propose','accept','revise','cancel','fulfill'):return None,'invalid_action'
    if not isinstance(topic,str) or not 2<=len(topic.strip())<=MAX_TOPIC:return None,'invalid_topic'
    topic=topic.strip()
    if not _participants_ok(participants):return None,'invalid_participants'
    if speaker!='bot' and speaker not in participants:return None,'speaker_not_participant'
    detail=str(detail or '')[:MAX_DETAIL]
    due=str(due or '')[:120]
    if action=='propose':
        identifier=agreement_identity(source_scope,source_event,topic)
        existing=db.execute('SELECT id,status,revision FROM memory_agreements WHERE id=?',(identifier,)).fetchone()
        if existing:return None,'already_registered'
        if db.execute("SELECT count(*) FROM memory_agreements WHERE status IN ('proposed','accepted','uncertain')").fetchone()[0]>=MAX_OPEN:
            return None,'agreement_slots_full'
        row={'id':identifier,'revision':1,'topic':topic,'detail':detail,'speaker':speaker,
             'participants':participants,'source_scope':source_scope,'source_event':str(source_event)[:100],
             'audiences':_allowed_audiences(db,source_scope,speaker,participants,cfg,audiences),
             'status':'proposed','due':due,'created':now,'updated':now}
        db.execute('INSERT INTO memory_agreements VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (row['id'],row['revision'],row['topic'],row['detail'],row['speaker'],json.dumps(row['participants'],ensure_ascii=False),
                    row['source_scope'],row['source_event'],json.dumps(row['audiences'],ensure_ascii=False),row['status'],row['due'],now,now))
        return row,''
    # Everything else targets an existing open row by exact identity.
    identifier=str(agreement_identity(source_scope,source_event,topic))
    row=db.execute('SELECT id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated FROM memory_agreements WHERE id=?',(identifier,)).fetchone()
    if row is None:return None,'agreement_not_found'
    stored_participants=json.loads(row[5]) if row[5] else []
    if speaker!='bot' and speaker not in stored_participants and speaker not in participants:return None,'speaker_not_participant'
    if row[9] not in OPEN_STATUSES:return None,'agreement_closed'
    revision=row[1]+1
    if action=='accept':
        # Both sides are now real: the original speaker plus whoever accepted.
        merged=list(dict.fromkeys(stored_participants+list(participants)))
        status='accepted'
    elif action=='revise':
        merged=stored_participants
        status=row[9]
    elif action=='cancel':
        merged=stored_participants;status='canceled'
    else:
        merged=stored_participants;status='fulfilled'
    new_audiences=json.loads(row[8]) if row[8] else []
    if action=='revise' and audiences is not None:
        new_audiences=_allowed_audiences(db,source_scope,row[4],merged,cfg,audiences)
    db.execute('UPDATE memory_agreements SET revision=?,topic=?,detail=?,participants=?,audiences=?,status=?,due=?,updated=? WHERE id=? AND revision=?',
               (revision,topic,detail,json.dumps(merged,ensure_ascii=False),json.dumps(new_audiences,ensure_ascii=False),
                status,due or row[10],now,identifier,row[1]))
    return {'id':identifier,'revision':revision,'topic':topic,'status':status,'audiences':new_audiences},''


def promote_from_claims(db,scope,claims,now=None):
    """Summary-path promotion: open agreement claims with complete verbatim
    evidence enter the projection for their own scope only. Cross-scope
    application still requires an explicit revision — legacy claims are never
    granted new audiences in bulk. Existing rows are never downgraded."""
    now=time.time() if now is None else now
    # The table exists (Reflection.__init__ ran initialize); no DDL may run
    # inside the caller's open summary transaction.
    promoted=0
    for claim in claims or []:
        if not isinstance(claim,dict) or claim.get('kind')!='agreement':continue
        if claim.get('status') not in ('open','proposed','unknown'):continue
        topic=str(claim.get('about') or '').strip()
        if not 2<=len(topic)<=MAX_TOPIC:continue
        speaker=claim.get('speaker') or {}
        person=str(speaker.get('id') or '') if speaker.get('role')=='human' else 'bot'
        refs=claim.get('sourceRefs') or []
        event=str(refs[0].get('event') or '') if refs and isinstance(refs[0],dict) else ''
        if not event:continue
        identifier=agreement_identity(scope,event,topic)
        if db.execute('SELECT 1 FROM memory_agreements WHERE id=?',(identifier,)).fetchone():continue
        status='proposed' if claim.get('status') in ('proposed','unknown') else 'accepted'
        participants=[person] if person=='bot' else [person,'bot']
        db.execute('INSERT OR IGNORE INTO memory_agreements VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (identifier,1,topic,str(claim.get('text') or '')[:MAX_DETAIL],person,
                    json.dumps(participants,ensure_ascii=False),scope,event,json.dumps([scope]),status,
                    str(claim.get('due') or '')[:120],now,now))
        promoted+=1
    return promoted


def find_open(db,scope,topic,speaker):
    """Resolve the open row a revision targets: an exact-topic match inside
    this scope's visible rows (identity lookup is unnecessary because the
    caller passes the matched row's own source scope/event back). Speaker
    authorization is re-checked by apply_update on the stored participants."""
    topic=str(topic or '').strip()
    rows=db.execute("SELECT id,topic,source_scope,source_event,speaker,participants,status FROM memory_agreements WHERE status IN ('proposed','accepted','uncertain') ORDER BY updated DESC LIMIT 32").fetchall()
    for row in rows:
        if row[1].strip()!=topic:continue
        try:participants=json.loads(row[5])
        except (ValueError,TypeError):participants=[]
        if speaker!='bot' and speaker not in participants and speaker not in (row[4],):continue
        try:audiences=json.loads(db.execute('SELECT audiences FROM memory_agreements WHERE id=?',(row[0],)).fetchone()[0])
        except (ValueError,TypeError):audiences=[]
        if scope in audiences or row[2]==scope:return row
    return None


def tools(settings,event,db,scope,current):
    """The one constrained state tool. The model judges meaning and supplies
    the update; this module owns identity, participants, revision CAS and the
    program-side audience filter. No extraction model, no keyword engine."""
    import bot_pipeline
    work=event.get_extra('_hosted_pipeline') if hasattr(event,'get_extra') else None
    if not isinstance(work,bot_pipeline.Work) or work.source not in ('message','deferred_group'):return []
    cfg=(settings.get('config') or {}).get('behavior') or {}
    if cfg.get('memory',True) is False:return []
    from astrbot.core.agent.tool import FunctionTool
    class UpdateAgreement(FunctionTool):
        async def call(self,context,action='',topic='',detail='',due='',speakerRole='user',participants=None,audiences=None):
            if not current():raise asyncio.CancelledError('stale agreement update')
            speaker=work.subject if speakerRole=='user' else 'bot'
            people=list(participants or [])
            people=[str(v) for v in people if isinstance(v,(str,int))]
            if speaker not in people:people.append(speaker)
            if action=='propose':
                row,error=apply_update(db,'propose',topic,speaker,people,scope,work.event_id,audiences,detail,due,cfg=cfg)
            else:
                open_row=find_open(db,scope,topic,speaker)
                if open_row is None:return json.dumps({'ok':False,'error':'agreement_not_found'},ensure_ascii=False)
                row,error=apply_update(db,action,topic,speaker,people,open_row[2],open_row[3],audiences,detail,due,cfg=cfg)
            if error:return json.dumps({'ok':False,'error':error},ensure_ascii=False)
            db.commit()
            return json.dumps({'ok':True,'id':row['id'],'revision':row['revision'],'status':row['status'],
                               'topic':row['topic'],'audiences':row.get('audiences') or []},ensure_ascii=False)
    return [UpdateAgreement(name='hosted_update_agreement',
        description='登记、接受、修订、取消或完成一项真实约定或待办。只有当本轮对话里确实形成了明确约定、接受、改变主意或完成时才调用；不要把普通闲聊、玩笑或单方面想法登记为约定。participants填写参与方真实QQ号（自己代表机器人时填"bot"）；speakerRole说明这个约定是谁提出或确认的；audiences填写该约定适用的会话（如当前群或某人私聊），程序会按真实权限过滤，模型建议不等于授权。时间不明确时due保持原话，不要编造具体时间。',
        parameters={'type':'object','properties':{
            'action':{'type':'string','enum':['propose','accept','revise','cancel','fulfill']},
            'topic':{'type':'string','maxLength':MAX_TOPIC},
            'detail':{'type':'string','maxLength':MAX_DETAIL},
            'due':{'type':'string','maxLength':120},
            'speakerRole':{'type':'string','enum':['user','bot']},
            'participants':{'type':'array','items':{'type':'string','maxLength':20},'maxItems':8},
            'audiences':{'type':'array','items':{'type':'string','maxLength':64},'maxItems':MAX_AUDIENCES}},
            'required':['action','topic'],'additionalProperties':False})]
