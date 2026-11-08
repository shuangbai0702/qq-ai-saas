"""Same-bot, trusted-QQ memory sharing; original records keep their source scope.

Callers own one bot's SQLite connection. This module never opens another bot's
database or infers a person from chat text, nicknames, or model output.
"""
import hashlib,json,re,time
import message_provenance


def shared_person(scope,subject):
    return bool(re.fullmatch(r'\d{5,20}',str(subject)) and
                (scope=='bot_shared' or scope=='private:'+str(subject) or re.fullmatch(r'group:\d{4,20}',scope)))


def person_filter(scope,subject,alias=''):
    prefix=alias+'.' if alias else ''
    if shared_person(scope,subject):
        return prefix+"subject=? AND ("+prefix+"scope LIKE 'group:%' OR "+prefix+"scope='bot_shared' OR "+prefix+"scope=?)",(subject,'private:'+subject)
    return prefix+'scope=? AND '+prefix+'subject=?',(scope,subject)


def value_hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def memory_audience(context,record):
    """Internal same-bot read, not permission to disclose to the destination.

    Callers select relevant people from this bot's database first. Scope
    provenance remains attached; identity and external tool permissions never
    come from the text. Unknown origins cannot establish trusted provenance.
    """
    if not isinstance(context,dict) or not isinstance(record,dict):return False
    origin=record.get('source_scope')
    if isinstance(origin,str) and origin and origin==context.get('scope'):return True
    return valid_scope(origin) and valid_scope(context.get('scope'))


def valid_scope(scope):
    return isinstance(scope,str) and bool(re.fullmatch(r'(?:self|bot_shared|private:\d{5,20}|group:\d{4,20})',scope))


def core_note_visible(scopes,scope,owner=''):
    """Internal read of the single core-note region, with known sources.

    Unknown provenance cannot establish trusted reference data. Such rows remain
    available to the internal/admin path and the owner's private briefing.
    """
    private_owner=bool(owner) and scope=='private:'+str(owner)
    if not isinstance(scopes,list) or not scopes or '*' in scopes:return scope=='self' or private_owner
    if scopes==[scope] and isinstance(scope,str) and scope:return True
    return valid_scope(scope) and all(valid_scope(x) for x in scopes)


def core_note_legacy(scopes):
    return not isinstance(scopes,list) or not scopes or '*' in scopes


def retained_context_visible(db,messages,scope,owner=''):
    """Validate platform provenance without reclassifying internal reads.

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
        memory_record=(value.get('kind')=='recent_uncompressed_message' or isinstance(value.get('summary'),str)
                       or {'name','value','source_event'}<=set(value))
        # Other capability records legitimately use '*' as an aggregate
        # interest scope. They are not message/fact provenance envelopes.
        if memory_record and origin is not None and not valid_scope(origin):return False
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
    # Source/person selection is internal relevance, not public disclosure.
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
    origin=row[10];event=row[2]
    if origin=='bot_shared':
        try:
            channel,identifier=json.loads(event)
            if valid_scope(channel):origin,event=channel,identifier
        except (ValueError,TypeError):pass
    return {'name':row[0],'value':row[1],'source_event':event,'version':row[3],
            'remembered_at':row[4],'kind':row[5],'origin':row[7],
            'source_scope':origin,'subject':subject,'recall_reason':reason}


def summary_rows(db,scope,subject='',limit=1000):
    # Other conversations enter recall only with protocol-backed participation
    # evidence for the current QQ, never because its ID appears in model text.
    # The same person's private material and protocol-backed participation
    # are relevant in both directions. Mentioning an ID in prose is no proof.
    if shared_person(scope,subject):
        return db.execute('''SELECT s.id,s.text,s.sources,s.updated,s.scope FROM memory_summaries s
          WHERE s.scope=? OR s.scope='bot_shared' OR (s.scope LIKE 'group:%' AND EXISTS (SELECT 1 FROM memory_summary_people p WHERE p.summary_id=s.id AND p.subject=?))
          OR s.scope=? ORDER BY s.updated DESC,s.id DESC LIMIT ?''',(scope,subject,'private:'+str(subject),min(1000,limit))).fetchall()
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


def pending_messages(db,scope,subject,now,limit=16,budget=3000,cross_scope=True,people=None,owner=''):
    """Uncompressed recent material for this scope's request view.

    ``cross_scope=False`` keeps this conversation's own rows. The historical
    ``cross_scope=True`` means ONLY other conversations' rows. The caller
    supplies protocol-backed people, never model-invented identities. This
    function reads one bot's existing pool and creates no replicated history.
    """
    if not shared_person(scope,subject):return []
    if cross_scope:
        from shared_context import active,recent
        if active(db):return recent(db,limit,budget)
    if not cross_scope:
        where,args='scope=? AND subject=?',(scope,subject)
    else:
        persons=list(dict.fromkeys(str(p) for p in [subject,*(people or []),owner] if shared_person(scope,str(p))))[:16]
        marks=','.join('?' for _ in persons)
        where,args='subject IN ('+marks+") AND scope<>? AND (scope LIKE 'group:%' OR scope='private:'||subject)",tuple(persons)+(scope,)
    columns='scope,subject,event,question,answer,at'
    if cross_scope:
        sql='WITH ranked AS (SELECT '+columns+''',rowid AS observed_order,
            row_number() OVER (PARTITION BY scope,subject ORDER BY at DESC,rowid DESC) AS source_rank
            FROM memory_pending WHERE '''+where+''' AND at>? AND at<=?)
            SELECT '''+columns+''',observed_order FROM ranked
            ORDER BY source_rank,at DESC,observed_order DESC LIMIT ?'''
    else:sql='SELECT '+columns+',rowid FROM memory_pending WHERE '+where+' AND at>? AND at<=? ORDER BY at DESC,rowid DESC LIMIT ?'
    raw=db.execute(sql,args+(now-7*86400,now,64 if cross_scope else min(16,max(1,limit)))).fetchall()
    observed_order={(row[0],str(row[2])):row[6] for row in raw}
    rows=[row[:6] for row in raw]
    if cross_scope:
        # First pass reserves one recent observation per person/source. Extra
        # rows fill the remaining budget; a busy group cannot monopolise it.
        first=[];rest=[];seen=set()
        for row in rows:
            key=(row[0],row[1])
            (rest if key in seen else first).append(row);seen.add(key)
        first.sort(key=lambda r:(r[1]!=str(owner),-r[5]))
        rows=first+rest
    result=[];used=0
    for source,person,event,question,answer,at in rows:
        if not valid_scope(source):continue
        if cross_scope:
            provenance=message_provenance.get(db,source,event)
            # Legacy aggregate Work text is not proven single-author speech.
            item={'kind':'recent_uncompressed_message','role':'member','source_scope':source,'source_event':event,
                  'sender_id':person,'at':at,'text':question[:400]}
            if len(question)>400:item['excerpt']=True
            if provenance.get('sender_id')==person:
                item.update({k:provenance[k] for k in ('sender_name','mentions','reply_to') if provenance.get(k)})
                if person==str(owner):item['attention']='[important]'
            else:item['attribution']='legacy_author_unverified'
            size=len(json.dumps(item,ensure_ascii=False,separators=(',',':')))
            if used+size>budget:continue
            result.append(item);used+=size
            if len(result)>=min(16,max(1,limit)):break
            continue
        question=question[:400];answer=answer[:400]
        if used+len(question)+len(answer)>budget:continue
        result.append({'kind':'recent_uncompressed_message','source_scope':source,'sender_id':person,'source_event':event,'at':at,'age_seconds':max(0,int(now-at)),'user':question,'bot':answer})
        result[-1]['source']=message_provenance.get(db,source,event)
        used+=len(question)+len(answer)
    if cross_scope and result and len(result)<min(16,max(1,limit)):
        # Read actual destination sends, never treat pending.answer (possibly
        # a whole multi-author Work) as a reply to this particular person.
        sources=list(dict.fromkeys(r['source_scope'] for r in result))
        marks=','.join('?' for _ in sources)
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'dialogue_feedback' in tables:
            sent=db.execute('SELECT scope,event,subject,at,reply FROM dialogue_feedback WHERE scope IN ('+marks+') AND at>? AND at<=? ORDER BY at DESC LIMIT 4',tuple(sources)+(now-7*86400,now)).fetchall()
            for source,event,person,at,text in sent:
                if str(person) not in persons:continue
                proof=message_provenance.get(db,source,event,'outgoing')
                if proof.get('receipt')!='confirmed' or not proof.get('message_ids'):continue
                item={'kind':'recent_uncompressed_message','role':'self','source_scope':source,'source_event':event,
                      'recipient_id':person,'at':at,'text':text[:400],
                      'receipt':'confirmed','message_ids':proof['message_ids']}
                if len(text)>400:item['excerpt']=True
                size=len(json.dumps(item,ensure_ascii=False,separators=(',',':')))
                if used+size>budget:continue
                result.append(item);used+=size
                if len(result)>=min(16,max(1,limit)):break
    # Equal protocol timestamps are common. Preserve ingestion order instead
    # of sorting new event IDs ahead of retained rows and resending their text.
    return sorted(result,key=lambda r:(r['at'],observed_order.get((r['source_scope'],str(r['source_event'])),max(observed_order.values(),default=0)+1)))
