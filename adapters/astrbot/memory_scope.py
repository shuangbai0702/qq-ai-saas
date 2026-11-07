"""Same-bot, trusted-QQ memory sharing; original records keep their source scope.

Callers own one bot's SQLite connection. This module never opens another bot's
database or infers a person from chat text, nicknames, or model output.
"""
import hashlib,json,re,time
import message_provenance


def shared_person(scope,subject):
    return bool(re.fullmatch(r'\d{5,20}',str(subject)) and
                (scope=='private:'+str(subject) or re.fullmatch(r'group:\d{4,20}',scope)))


def person_filter(scope,subject,alias=''):
    prefix=alias+'.' if alias else ''
    if shared_person(scope,subject):
        return prefix+"subject=? AND ("+prefix+"scope LIKE 'group:%' OR "+prefix+"scope=?)",(subject,'private:'+subject)
    return prefix+'scope=? AND '+prefix+'subject=?',(scope,subject)


def value_hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def memory_audience(context,record):
    """One per-record audience gate shared by rolling, semantic candidates,
    search and experience reads.

    scope='self' is not a public licence: merged self/big material is shown
    only in its home scope or the owner's private chat. Private content stays
    in its original chat; the same person keeps their own records across their
    conversations (the existing same-QQ policy - participation membership is
    enforced by the candidate queries, this gate decides direction only).
    """
    if not isinstance(context,dict) or not isinstance(record,dict):return False
    scope=str(context.get('scope') or '');subject=str(context.get('subject') or '');owner=str(context.get('owner') or '')
    origin=record.get('source_scope')
    if not isinstance(origin,str) or not origin:return False
    if origin==scope:return True
    if origin=='self':return scope=='self' or bool(owner) and scope=='private:'+owner
    if origin.startswith('private:'):
        return bool(subject) and origin=='private:'+subject and shared_person(scope,subject)
    if origin.startswith('group:'):
        return bool(subject) and shared_person(scope,subject)
    return False


def core_note_visible(scopes,scope,owner=''):
    """Audience for the single core-note region, from its recorded sources.

    Unknown provenance cannot establish a public audience. Such rows remain
    available to the internal/admin path and the owner's private briefing.
    """
    private_owner=bool(owner) and scope=='private:'+str(owner)
    if not isinstance(scopes,list) or not scopes or '*' in scopes:return scope=='self' or private_owner
    unique={str(x) for x in scopes if isinstance(x,str) and x}
    if not unique or unique=={'self'}:return scope=='self' or private_owner
    if len(unique)==1:return scope in unique
    return scope=='self' or bool(owner) and scope=='private:'+str(owner)


def core_note_legacy(scopes):
    return not isinstance(scopes,list) or not scopes or '*' in scopes


def retained_context_visible(db,messages,scope,owner=''):
    """Reject known forbidden memory already present in a committed wire.

    Filtering the next snapshot cannot withdraw the earlier snapshot from an
    append-only request. Inspect platform envelopes only; ordinary quoted JSON
    is not provenance. A false result requires a legal rebuild, never a patch
    to old messages. Historical merged-away participation is not invented.
    """
    from context_protocol import READONLY_CURRENT
    from context_transport import EVENT_CONTEXT_CURRENT
    def visit(value):
        if isinstance(value,list):return all(visit(x) for x in value)
        if not isinstance(value,dict):return True
        note=value.get('persona_core_note')
        if isinstance(note,dict) and note.get('text') and not core_note_visible(note.get('source_scopes'),scope,owner):return False
        origin=value.get('source_scope')
        if isinstance(origin,str) and isinstance(value.get('summary'),str):
            if origin=='self' and scope!='self' and not (owner and scope=='private:'+str(owner)):
                origins=value.get('source_scopes')
                if not isinstance(origins,list) or not set(origins)<={'self',scope}:return False
            # Same-person query recall intentionally crosses group/private
            # scopes. Only rolling-background rows establish this mismatch;
            # do not mistake legitimate personal recall for global background.
            rolling=value.get('recall_reason')=='rolling_background'
            if rolling and origin.startswith('private:') and origin!=scope:return False
            if rolling and origin.startswith('group:') and origin!=scope and scope!='self':
                if not scope.startswith('private:'):return False
                identifier=value.get('id')
                # A live summary with no participation is a proven mismatch.
                # Missing legacy evidence is reported separately, not inferred.
                if db.execute('SELECT 1 FROM memory_summaries WHERE id=?',(identifier,)).fetchone() and not db.execute('SELECT 1 FROM memory_summary_people WHERE summary_id=? AND subject=?',(identifier,scope[8:])).fetchone():return False
        return all(visit(x) for x in value.values())
    for message in messages:
        content=message.get('content') if isinstance(message,dict) and message.get('role')=='user' else None
        if not isinstance(content,str) or not any(content.startswith(marker+'\n') for marker in (READONLY_CURRENT,EVENT_CONTEXT_CURRENT)):continue
        try:value=json.loads(content.split('\n',1)[1])
        except (TypeError,ValueError):return False
        if not visit(value):return False
        for op in value.get('readonlyChanges',[]) if isinstance(value,dict) else []:
            if isinstance(op,list) and len(op)==3 and op[0]=='set' and op[1] and op[1][-1]=='persona_core_note' and not visit({'persona_core_note':op[2]}):return False
    return True


def blocked_facts(db,scope,subject):
    """Hard whole-name forgets and per-value correction blocks for this person.

    Correction rows (block_all=0) only reject their recorded values; whole-name
    forget rows (block_all=1) hide the field for every automatic origin until a
    sanctioned explicit restore removes them. A corrected value is blocked for
    this person whatever field a later extraction renames it to, never across
    different people.
    """
    hard=set();rejected={};values=set()
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_blocks'").fetchone():return hard,rejected,values
    where,args=person_filter(scope,subject,'b')
    for name,block_all,raw in db.execute('SELECT b.name,b.block_all,b.rejected_values FROM memory_blocks b WHERE '+where,args):
        if block_all:hard.add(name);continue
        try:entries=json.loads(raw) if raw else []
        except (ValueError,TypeError):entries=[]
        if isinstance(entries,list):
            blocked={e['v'] for e in entries if isinstance(e,dict) and isinstance(e.get('v'),str)}
            rejected.setdefault(name,set()).update(blocked);values.update(blocked)
    if hard:
        # A whole-name forget also suppresses the values already recorded for
        # that field, so a later extraction cannot smuggle them back under a
        # model-renamed field. Explicit restore removes the row itself.
        marks=','.join('?' for _ in hard)
        fwhere,fargs=person_filter(scope,subject,'f')
        for (value,) in db.execute('SELECT f.value FROM facts f WHERE '+fwhere+' AND f.name IN ('+marks+')',fargs+tuple(sorted(hard))):
            if value is not None:values.add(value_hash(value))
    return hard,rejected,values


def record_correction(db,scope,subject,name,old_value,event,quote='',now=None):
    """Merge one verified correction's evidence into the value-level block row.

    Ordinary corrections add the old value's hash and bump revision; the UPSERT
    never touches block_all, so an existing whole-name forget row cannot be
    downgraded to a value-level row. Must run inside the caller's transaction
    together with the fact replacement it belongs to.
    """
    where,args=person_filter(scope,subject,'b')
    row=db.execute('SELECT b.rejected_values,b.revision FROM memory_blocks b WHERE '+where+' AND b.name=? AND b.scope=?',args+(name,scope)).fetchone()
    try:entries=[e for e in json.loads(row[0]) if isinstance(e,dict)] if row and row[0] else []
    except (ValueError,TypeError):entries=[]
    entry={'v':value_hash(old_value),'event':str(event or '')[:100],'quote':str(quote or '')[:200],'at':round(now if now is not None else time.time(),3)}
    entries=[e for e in entries if e.get('v')!=entry['v']][-15:]+[entry]
    revision=(row[1] or 0)+1 if row else 1
    db.execute('''INSERT INTO memory_blocks(scope,subject,name,block_all,rejected_values,revision) VALUES(?,?,?,0,?,?)
      ON CONFLICT(scope,subject,name) DO UPDATE SET rejected_values=excluded.rejected_values,revision=excluded.revision''',
      (scope,subject,name,json.dumps(entries,ensure_ascii=False),revision))
    return revision


def release_value(db,scope,subject,name,value):
    """Sanctioned explicit restore: lift this value's block, keep the rest.

    A whole-name forget row is cleared only by this explicit path (the existing
    记住-after-忘记 command); other values' correction blocks stay untouched.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_blocks'").fetchone():return False
    where,args=person_filter(scope,subject,'b')
    changed=False;target=value_hash(value)
    for rowid,block_all,raw in db.execute('SELECT b.rowid,b.block_all,b.rejected_values FROM memory_blocks b WHERE '+where+' AND b.name=?',args+(name,)).fetchall():
        if block_all:
            db.execute('DELETE FROM memory_blocks WHERE rowid=?',(rowid,));changed=True;continue
        try:entries=[e for e in json.loads(raw) if isinstance(e,dict)] if raw else []
        except (ValueError,TypeError):entries=[]
        kept=[e for e in entries if e.get('v')!=target]
        if len(kept)!=len(entries):
            changed=True
            if kept:db.execute('UPDATE memory_blocks SET rejected_values=? WHERE rowid=?',(json.dumps(kept,ensure_ascii=False),rowid))
            else:db.execute('DELETE FROM memory_blocks WHERE rowid=?',(rowid,))
    return changed


def fact_rows(db,scope,subject,limit=200):
    where,args=person_filter(scope,subject,'f')
    hard,rejected,blocked_values=blocked_facts(db,scope,subject)
    # Explicit records win over automatic captures. Among equal origins the
    # latest source wins; legacy duplicate records are read without data loss.
    # Value-level correction blocks drop the rejected values for this person
    # (including under a model-renamed field); a name whose value was blocked
    # keeps its replacement visible.
    # Autonomous research task records keep their rows in storage for audit
    # and rollback, but they are tool task outcomes — never persona facts —
    # so every recall pool excludes them by their trusted origin.
    rows=db.execute('''SELECT f.name,f.value,f.event,f.version,f.updated,coalesce(m.kind,'stable') AS kind,
        coalesce(m.importance,.8) AS importance,coalesce(m.origin,'explicit') AS origin,
        coalesce(m.last_recalled,0) AS last_recalled,coalesce(m.recalls,0) AS recalls,f.scope AS source_scope
      FROM facts f LEFT JOIN fact_metadata m ON f.scope=m.scope AND f.subject=m.subject AND f.name=m.name
      WHERE '''+where+''' AND f.active=1 AND coalesce(m.origin,'explicit')<>'autonomous_result'
      ORDER BY (coalesce(m.origin,'explicit')='explicit') DESC,f.updated DESC,f.version DESC,f.scope''',args).fetchall()
    best={}
    for row in rows:
        name=row[0]
        if name in hard or value_hash(row[1]) in blocked_values:continue
        if name not in best:best[name]=row
    return list(best.values())[:min(200,max(1,limit))]


def fact_item(row,subject,reason):
    return {'name':row[0],'value':row[1],'source_event':row[2],'version':row[3],
            'remembered_at':row[4],'kind':row[5],'origin':row[7],
            'source_scope':row[10],'subject':subject,'recall_reason':reason}


def summary_rows(db,scope,subject='',limit=1000):
    # Other conversations enter recall only with protocol-backed participation
    # evidence for the current QQ, never because its ID appears in model text.
    if shared_person(scope,subject):
        return db.execute('''SELECT s.id,s.text,s.sources,s.updated,s.scope FROM memory_summaries s
          WHERE s.scope=? OR ((s.scope LIKE 'group:%' OR s.scope=?) AND EXISTS (SELECT 1 FROM memory_summary_people p WHERE p.summary_id=s.id AND p.subject=?))
          ORDER BY s.updated DESC,s.id DESC LIMIT ?''',(scope,'private:'+subject,subject,min(1000,limit))).fetchall()
    return db.execute('SELECT id,text,sources,updated,scope FROM memory_summaries WHERE scope=? ORDER BY updated DESC,id DESC LIMIT ?',(scope,min(1000,limit))).fetchall()


def summary_item(row,reason=None,db=None):
    result={'id':row[0],'summary':row[1],'source_events':json.loads(row[2]),'at':row[3],
            'source_scope':row[4],'kind':'conversation_summary_not_verified_facts'}
    if reason:result['recall_reason']=reason
    if db is not None:
        from summary_material import summary_claims,protected_claim
        claims=[claim for claim in summary_claims(db,row[0]) if protected_claim(claim)]
        if claims:result['protected_claims']=claims
    return result


def pending_messages(db,scope,subject,now,limit=16,budget=3000,cross_scope=True):
    if not shared_person(scope,subject):return []
    where,args=person_filter(scope,subject)
    source='scope!=?' if cross_scope else 'scope=?'
    rows=db.execute('SELECT scope,subject,event,question,answer,at FROM memory_pending WHERE '+where+' AND '+source+' AND at>? AND at<=? ORDER BY at DESC,rowid DESC LIMIT ?',args+(scope,now-7*86400,now,min(16,max(1,limit)))).fetchall()
    result=[];used=0
    for source,person,event,question,answer,at in rows:
        question=question[:400];answer=answer[:400]
        if used+len(question)+len(answer)>budget:continue
        result.append({'kind':'recent_uncompressed_message','source_scope':source,'sender_id':person,'source_event':event,'at':at,'age_seconds':max(0,int(now-at)),'user':question,'bot':answer})
        result[-1]['source']=message_provenance.get(db,source,event)
        used+=len(question)+len(answer)
    return list(reversed(result))
