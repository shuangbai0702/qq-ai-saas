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
import asyncio,hashlib,json,time,re

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
    db.execute('''CREATE TABLE IF NOT EXISTS memory_agreements(
      id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 1,topic TEXT NOT NULL,detail TEXT NOT NULL,
      speaker TEXT NOT NULL,participants TEXT NOT NULL,source_scope TEXT NOT NULL,source_event TEXT NOT NULL,
      audiences TEXT NOT NULL,status TEXT NOT NULL,due TEXT,created REAL NOT NULL,updated REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS memory_agreements_updated ON memory_agreements(updated)')
    columns={r[1] for r in db.execute('PRAGMA table_info(memory_agreements)')}
    for name,kind in (('handoff_id','TEXT'),('task_revision','INTEGER'),('evidence',"TEXT NOT NULL DEFAULT '[]'")):
        if name not in columns:db.execute('ALTER TABLE memory_agreements ADD COLUMN '+name+' '+kind)


def agreement_identity(scope,event,topic):
    return hashlib.sha256('\x00'.join(('agreement',scope,str(event),topic.strip())).encode()).hexdigest()[:24]


def _valid_scope(value):
    return isinstance(value,str) and bool(re.fullmatch(r'(?:self|private:[1-9][0-9]{4,19}|group:[1-9][0-9]{3,19})',value))


def _participants_ok(values):
    return isinstance(values,list) and 1<=len(values)<=8 and all(
        isinstance(v,str) and (v=='bot' or re.fullmatch(r'[1-9][0-9]{4,19}',v)) for v in values)


def visible(db,scope,now=None,background=False,owner=''):
    """Internal state view; audiences denotes applicability, not disclosure.

    Terminal rows stay briefly for reference and then age out; they never
    masquerade as current commitments. Preserve conditions across scopes and
    explicitly distinguish relevant background from rules applying here.
    """
    now=time.time() if now is None else now
    result=[]
    for row in db.execute("SELECT id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated FROM memory_agreements ORDER BY (status IN ('proposed','accepted','uncertain')) DESC,updated DESC"):
        try:audiences=json.loads(row[8])
        except (ValueError,TypeError):continue
        if not isinstance(audiences,list):continue
        if row[9] not in OPEN_STATUSES and now-row[12]>AGREEMENT_TTL:continue
        try:people=json.loads(row[5])
        except (ValueError,TypeError):continue
        if not _participants_ok(people):continue
        applies=scope in audiences
        if owner and set(people)!={str(owner),'bot'}:continue
        if not applies and not (background and owner and str(owner) in people):continue
        result.append({'id':row[0],'revision':row[1],'topic':row[2],'status':row[9],
                       'due':row[10] or '','speaker':row[4],'participants':people,
                       'source_scope':row[6],'source_event':row[7],'updated':row[12],
                       'detail':row[3],'applies_here':applies,'audiences':audiences})
        # Never evict a live accepted rule merely because eight newer topics
        # exist. Open slots already have a write-time bound; terminals are brief.
        if row[9] not in OPEN_STATUSES and len(result)>=max(8,MAX_OPEN):break
    return result


def context_view(db,scope,owner=None):
    """Bounded "当前有效事项" view for a new request. The view is the bot's
    own effective commitment state — honored when it applies to the current
    conversation, and never a source of new permissions or identity."""
    if owner is not None and not owner:return []
    rows=visible(db,scope,background=True,owner=owner or '')
    return rows


def agreements_stamp(db,scope):
    """Content-free revision fingerprint of what this scope currently sees.

    Rides the existing context fence: a mid-Work revision or cancellation
    invalidates the stale wire candidate the same way a summary completion
    does, instead of letting an already-stale reply ship."""
    # Include background owner agreements too: a target revision may change
    # what a currently-running model knows before it dispatches effects.
    # Fingerprint all live revisions, independent of projection quota/scope.
    # This is small (MAX_OPEN) and cannot manufacture a retirement permit.
    rows=db.execute("SELECT id,revision,status FROM memory_agreements WHERE status IN ('proposed','accepted','uncertain') OR updated>? ORDER BY id",(time.time()-AGREEMENT_TTL,)).fetchall()
    return hashlib.sha256(json.dumps([list(r) for r in rows],
                                     ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:16]


def _allowed_audiences(db,scope,speaker,participants,cfg,proposed):
    """Program-side audience decision. The model proposes; this filter
    authorizes: the current scope, private chats of real participants, and —
    only for the configured owner — groups the bot actually serves."""
    allowed={scope} if _valid_scope(scope) else set()
    # Membership suggested by a model cannot grant a new private audience.
    # Only the authenticated human's own private view is eligible.
    if speaker!='bot' and speaker in (participants or []):allowed.add('private:'+speaker)
    owner=str((cfg or {}).get('ownerUin') or '')
    if speaker==owner and owner:
        for group in (cfg or {}).get('groups') or []:
            if isinstance(group,str) and group.isdigit():allowed.add('group:'+group)
    result=[value for value in dict.fromkeys(proposed or []) if value in allowed][:_MAX_AUD]
    return result or ([scope] if _valid_scope(scope) else [])

_MAX_AUD=MAX_AUDIENCES


def apply_update(db,action,topic,speaker,participants,source_scope,source_event,audiences=None,
                 detail='',due='',cfg=None,now=None,expected_revision=None,evidence=None):
    """One CAS'd state write. Returns (row dict, error string). No keyword
    guessing: the caller's model already judged this a real commitment."""
    now=time.time() if now is None else now
    initialize(db)
    if action not in ('propose','accept','revise','cancel','fulfill','expire'):return None,'invalid_action'
    if not isinstance(topic,str) or not 2<=len(topic.strip())<=MAX_TOPIC:return None,'invalid_topic'
    if action not in ('propose','accept','revise','cancel','expire','fulfill'):return None,'invalid_action'
    topic=topic.strip()
    if not _valid_scope(source_scope) or not str(source_event):return None,'invalid_source'
    if not _participants_ok(participants):return None,'invalid_participants'
    owner=str((cfg or {}).get('ownerUin') or '')
    if cfg is not None and (not owner or (set(participants)!={owner,'bot'} if action=='propose' else not set(participants)<={owner,'bot'})):return None,'owner_agreement_required'
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
        db.execute('INSERT INTO memory_agreements(id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (row['id'],row['revision'],row['topic'],row['detail'],row['speaker'],json.dumps(row['participants'],ensure_ascii=False),
                    row['source_scope'],row['source_event'],json.dumps(row['audiences'],ensure_ascii=False),row['status'],row['due'],now,now))
        if evidence:db.execute('UPDATE memory_agreements SET evidence=? WHERE id=?',(json.dumps([evidence],ensure_ascii=False),identifier))
        return row,''
    # Everything else targets an existing open row by exact identity.
    identifier=str(agreement_identity(source_scope,source_event,topic))
    row=db.execute('SELECT id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated FROM memory_agreements WHERE id=?',(identifier,)).fetchone()
    if row is None:return None,'agreement_not_found'
    stored_participants=json.loads(row[5]) if row[5] else []
    if cfg is not None and set(stored_participants)!={owner,'bot'}:return None,'owner_agreement_required'
    if speaker not in stored_participants:return None,'speaker_not_participant'
    if expected_revision is not None and row[1]!=expected_revision:return None,'agreement_revision_changed'
    if row[9] not in OPEN_STATUSES:return None,'agreement_closed'
    revision=row[1]+1
    if action=='accept':
        # Both sides are now real: the original speaker plus whoever accepted.
        if speaker==row[4] or len(stored_participants)<2:return None,'other_party_confirmation_required'
        merged=stored_participants
        status='accepted'
    elif action=='revise':
        merged=stored_participants
        # Changed terms need renewed agreement; they cannot inherit assent.
        status='proposed' if detail and detail!=row[3] or due and due!=row[10] else row[9]
    elif action=='cancel':
        merged=stored_participants;status='canceled'
    elif action=='expire':
        merged=stored_participants;status='expired'
    else:
        merged=stored_participants;status='fulfilled'
    new_audiences=json.loads(row[8]) if row[8] else []
    if action=='revise' and audiences is not None:
        proposed=_allowed_audiences(db,source_scope,speaker,merged,cfg,audiences)
        new_audiences=proposed if speaker!='bot' else [s for s in proposed if s in new_audiences] or new_audiences
    proposer=speaker if action=='revise' and status=='proposed' else row[4]
    updated=db.execute('UPDATE memory_agreements SET revision=?,topic=?,detail=?,speaker=?,participants=?,audiences=?,status=?,due=?,updated=? WHERE id=? AND revision=?',
               (revision,topic,detail or row[3],proposer,json.dumps(merged,ensure_ascii=False),json.dumps(new_audiences,ensure_ascii=False),
                status,due or row[10],now,identifier,row[1]))
    if updated.rowcount!=1:return None,'agreement_revision_changed'
    if evidence:
        old_evidence=db.execute('SELECT evidence FROM memory_agreements WHERE id=?',(identifier,)).fetchone()[0]
        try:items=json.loads(old_evidence)
        except (ValueError,TypeError):items=[]
        db.execute('UPDATE memory_agreements SET evidence=? WHERE id=?',(json.dumps((items+[evidence])[-8:],ensure_ascii=False),identifier))
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
        if db.execute("SELECT count(*) FROM memory_agreements WHERE status IN ('proposed','accepted','uncertain')").fetchone()[0]>=MAX_OPEN:break
        # A protected claim alone does not prove mutual assent.
        roles={ref.get('role') for ref in refs if isinstance(ref,dict)}
        status='accepted' if claim.get('stage')=='accepted' and {'user','assistant'}<=roles else 'proposed'
        participants=[person] if person=='bot' else [person,'bot']
        db.execute('INSERT OR IGNORE INTO memory_agreements(id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (identifier,1,topic,str(claim.get('text') or '')[:MAX_DETAIL],person,
                    json.dumps(participants,ensure_ascii=False),scope,event,json.dumps([scope]),status,
                    str(claim.get('due') or '')[:120],now,now))
        promoted+=1
    return promoted


def find_open(db,scope,topic,speaker,agreement_id=''):
    """Resolve the open row a revision targets: an exact-topic match inside
    this scope's visible rows, optionally pinned to a returned agreement ID.
    The caller passes the matched row's own source scope/event back. Speaker
    authorization is re-checked by apply_update on the stored participants."""
    topic=str(topic or '').strip()
    rows=db.execute("SELECT id,topic,source_scope,source_event,speaker,participants,status FROM memory_agreements WHERE status IN ('proposed','accepted','uncertain') ORDER BY updated DESC LIMIT 32").fetchall()
    matches=[]
    for row in rows:
        if agreement_id and row[0]!=agreement_id:continue
        if row[1].strip()!=topic:continue
        try:participants=json.loads(row[5])
        except (ValueError,TypeError):participants=[]
        if speaker!='bot' and speaker not in participants and speaker not in (row[4],):continue
        try:audiences=json.loads(db.execute('SELECT audiences FROM memory_agreements WHERE id=?',(row[0],)).fetchone()[0])
        except (ValueError,TypeError):audiences=[]
        # A participant can revise/cancel from another conversation; audience
        # is applicability, not a restriction on internal lookup.
        if scope in audiences or row[2]==scope or speaker in participants:matches.append(row)
    return matches[0] if len(matches)==1 else None


def task_current(db,binding):
    if not binding:return True
    row=db.execute('SELECT revision,status,handoff_id FROM memory_agreements WHERE id=?',(binding.get('id'),)).fetchone()
    return bool(row and row[0]==binding.get('revision') and row[1]=='accepted')


def task_states(db):
    """Version fences for the existing PG goals; no chat bodies or scheduler."""
    return [{'id':r[0],'revision':r[1],'status':r[2],'handoffId':r[3],'taskRevision':r[4]}
            for r in db.execute('SELECT id,revision,status,handoff_id,task_revision FROM memory_agreements WHERE handoff_id IS NOT NULL')]


def settle_task(db,handoff_id,result):
    # An unknown/partial operation must retain its evidence and remain open.
    if not handoff_id or result.get('status')!='completed' or result.get('unfinished'):return
    effects=result.get('effects') or []
    if any(e.get('status')!='confirmed' for e in effects):return
    if any(t.get('status') in ('failed','unavailable') for t in result.get('toolResults',[])):return
    db.execute("UPDATE memory_agreements SET status='fulfilled',revision=revision+1,updated=? WHERE handoff_id=? AND revision=task_revision AND status='accepted'",(time.time(),handoff_id))


def tools(settings,event,db,scope,current):
    """The one constrained state tool. The model judges meaning and supplies
    the update; this module owns identity, participants, revision CAS and the
    program-side audience filter. No extraction model, no keyword engine."""
    import bot_pipeline
    work=event.get_extra('_hosted_pipeline') if hasattr(event,'get_extra') else None
    config=settings.get('config') or {}
    cfg={**(config.get('behavior') or {}),'groups':config.get('groups') or []}
    if cfg.get('memory',True) is False:return []
    owner=str(cfg.get('ownerUin') or '')
    if not owner:return []
    from astrbot.core.agent.tool import FunctionTool
    class UpdateAgreement(FunctionTool):
        async def call(self,context,action='',topic='',detail='',due='',speakerRole='user',participants=None,audiences=None,quote='',revision=None,agreement_id=''):
            if not current():raise asyncio.CancelledError('stale agreement update')
            if context.context.event is not event or not isinstance(work,bot_pipeline.Work) or work.source not in ('message','deferred_group') or event.get_extra('_hosted_tool_completion'):
                return json.dumps({'ok':False,'error':'real_message_work_required'})
            if work.subject!=owner:return json.dumps({'ok':False,'error':'owner_agreement_required'})
            if not isinstance(agreement_id,str) or agreement_id and not re.fullmatch(r'[a-f0-9]{24}',agreement_id):
                return json.dumps({'ok':False,'error':'invalid_agreement_id'})
            if agreement_id and action=='propose':return json.dumps({'ok':False,'error':'new_agreement_has_no_id'})
            speaker=work.subject if speakerRole=='user' else 'bot'
            if speakerRole not in ('user','bot'):return json.dumps({'ok':False,'error':'invalid_speaker'})
            if speakerRole=='user' and (not isinstance(quote,str) or not quote.strip() or quote not in event.get_message_str()):
                return json.dumps({'ok':False,'error':'current_user_evidence_required'})
            if speakerRole=='user':
                observed=db.execute('SELECT subject,question FROM memory_pending WHERE scope=? AND event=?',(scope,str(work.event_id))).fetchone()
                # A merged Work's text may include other people; only the
                # authenticated owner's original event can prove their assent.
                if not observed or observed[0]!=owner or quote not in observed[1]:
                    return json.dumps({'ok':False,'error':'current_user_evidence_required'})
            people=list(participants if participants is not None else [work.subject,'bot'])
            people=[str(v) for v in people if isinstance(v,(str,int))]
            if speaker not in people:people.append(speaker)
            if any(p not in (work.subject,'bot') for p in people):return json.dumps({'ok':False,'error':'unverified_participant'})
            if set(people)!={owner,'bot'}:return json.dumps({'ok':False,'error':'owner_agreement_required'})
            proof={'scope':scope,'event':work.event_id,'speaker':speaker,'quote':quote[:300] if speakerRole=='user' else ''}
            if action=='propose':
                row,error=apply_update(db,'propose',topic,speaker,people,scope,work.event_id,audiences,detail,due,cfg=cfg,evidence=proof)
            else:
                open_row=find_open(db,scope,topic,speaker,agreement_id)
                if open_row is None:return json.dumps({'ok':False,'error':'agreement_not_found'},ensure_ascii=False)
                stored=db.execute('SELECT revision FROM memory_agreements WHERE id=?',(open_row[0],)).fetchone()
                if type(revision) is not int or not stored or stored[0]!=revision:return json.dumps({'ok':False,'error':'agreement_revision_required'})
                # A scheduled agreement is fulfilled only by its task receipt.
                if action=='fulfill' and db.execute('SELECT handoff_id FROM memory_agreements WHERE id=?',(open_row[0],)).fetchone()[0]:
                    return json.dumps({'ok':False,'error':'task_receipt_required'})
                row,error=apply_update(db,action,topic,speaker,people,open_row[2],open_row[3],audiences,detail,due,cfg=cfg,expected_revision=revision,evidence=proof)
            if error:return json.dumps({'ok':False,'error':error},ensure_ascii=False)
            db.commit()
            # A successful local update is now known to this Work. Other
            # Works keep their old stamp and cannot dispatch stale effects.
            event.set_extra('_hosted_agreement_fence',(scope,agreements_stamp(db,scope)))
            return json.dumps({'ok':True,'id':row['id'],'revision':row['revision'],'status':row['status'],
                               'topic':row['topic'],'audiences':row.get('audiences') or [],'scheduling':'accepted且需要延后执行时，用propose_background_task关联agreement_id和agreement_revision；本工具本身不安排执行'},ensure_ascii=False)
    return [UpdateAgreement(name='hosted_update_agreement',
        description='管理本机器人与真实主人的明确约定或持续行为要求：形成、确认、修改、撤销或完成时及时登记，不等待摘要；普通聊天和单方面想法不登记。participants只填主人真实QQ与"bot"；speakerRole区分提出者和确认者；audiences是实际适用会话，不是允许披露私聊的授权。保留条件、例外和范围，不从旧聊天伪造新指令。用户来源必须填写当前真实消息逐字quote，更新必须填写revision，可用登记回执id填写agreement_id精确定位；接受需要另一方确认。未取得真实登记回执不声称已经生效；延后执行另用propose_background_task关联约定，登记不等于任务已安排。',
        parameters={'type':'object','properties':{
            'agreement_id':{'type':'string','pattern':'^[a-f0-9]{24}$'},
            'quote':{'type':'string','maxLength':300},'revision':{'type':'integer','minimum':1},
            'action':{'type':'string','enum':['propose','accept','revise','cancel','fulfill','expire']},
            'topic':{'type':'string','maxLength':MAX_TOPIC},
            'detail':{'type':'string','maxLength':MAX_DETAIL},
            'due':{'type':'string','maxLength':120},
            'speakerRole':{'type':'string','enum':['user','bot']},
            'participants':{'type':'array','items':{'type':'string','maxLength':20},'maxItems':8},
            'audiences':{'type':'array','items':{'type':'string','maxLength':64},'maxItems':MAX_AUDIENCES}},
            'required':['action','topic'],'additionalProperties':False})]
