"""Collect one bot's exact hot inventory into its existing shared batch.

The caller owns the transaction and the 35 completed-round anchor. Mirrored
pending/material rows are inputs, never new rounds or permission assertions.
"""
import copy,hashlib,json,math,re
from context_protocol import model_json,source
from semantic_batch import event_key,origin,discard_failed_service_inputs
from summary_material import snapshots,valid_material,remember,material_storage_view

# Disposable observations can outlive their old pending row. They are still
# real batch input, rather than a second memory authority or a new round.
OBSERVATION_POOLS=(('social_samples','subject','text','created'),
    ('relation_evidence','subject','quote','at'),('relation_events','subject',None,'at'),
    ('dialogue_feedback','subject',None,'at'),('companion_seen',None,None,'at'),
    ('companion_signals',None,'quote','at'))


def _channel(value):
    return value=='self' or isinstance(value,str) and re.fullmatch(r'(?:private:\d{5,20}|group:\d{4,20})',value) is not None


def _time(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def previous_summaries(db):
    """Full bot-local semantic products, with no original event ID indexes."""
    from memory_scope import valid_scope
    from summary_material import summary_claims,claim_view
    result=[]
    for identifier,scope,text,level in db.execute("SELECT s.id,s.scope,s.text,coalesce(l.kind,'small') FROM memory_summaries s LEFT JOIN memory_summary_levels l ON l.id=s.id ORDER BY s.id"):
        if not valid_scope(scope):continue
        origins=[r[0] for r in db.execute('SELECT scope FROM memory_summary_origins WHERE summary_id=? ORDER BY scope',(identifier,))] or [scope]
        if not all(valid_scope(x) for x in origins):raise ValueError('semantic_collection_summary_origin_unknown')
        people=[r[0] for r in db.execute('SELECT subject FROM memory_summary_people WHERE summary_id=? ORDER BY subject',(identifier,))]
        claims=[]
        for claim in summary_claims(db,identifier):
            view=claim_view(claim)
            for key in ('delivery','protected'):
                if key in claim:view[key]=claim[key]
            claims.append(view)
        result.append({'id':identifier,'scope':scope,'text':text,'level':level,'source_scopes':origins,'subjects':people,'claims':claims})
    return result


def _journals(db,tables):
    """Validate transport containers before exposing their original blocks."""
    import journal_storage
    rows=[]
    for table in ('fishbot_readonly_journals','memory_checkpoint_archives'):
        if table not in tables:continue
        columns={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
        if not {'scope','version','payload','sha'}<=columns:
            if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():raise ValueError('semantic_collection_journal_integrity')
            continue
        rows.extend(db.execute('SELECT scope,version,payload,sha FROM '+table+' WHERE payload IS NOT NULL'))
    if 'fishbot_operator_session_archives' in tables:
        for key,raw,sha in db.execute('SELECT scope,archive,sha FROM fishbot_operator_session_archives'):
            if not isinstance(raw,str) or hashlib.sha256(raw.encode()).hexdigest()!=sha:raise ValueError('semantic_collection_archive_integrity')
            value=json.loads(raw)['journal']
            if value is None:continue
            if not isinstance(value,dict):raise ValueError('semantic_collection_archive_shape')
            if value.get('scope')!=key:raise ValueError('semantic_collection_archive_scope')
            rows.append((key,value['version'],value['payload'],value['sha']))
    for key,version,payload,sha in rows:
        channel=json.loads(key)
        if channel!='bot_shared' and not _channel(channel):continue
        if not isinstance(payload,str) or hashlib.sha256(payload.encode()).hexdigest()!=sha:raise ValueError('semantic_collection_journal_integrity')
        def blob(scope,digest):
            row=db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(scope,digest)).fetchone()
            return row[0] if row else None
        if version==1:value=json.loads(payload)
        elif version==journal_storage.VERSION:value=journal_storage.decode(payload,key,blob)
        else:raise ValueError('semantic_collection_journal_version')
        if not isinstance(value,dict) or value.get('scope')!=key:raise ValueError('semantic_collection_journal_scope')
        for field in ('base','wire'):
            messages=value.get(field)
            if not isinstance(messages,list):raise ValueError('semantic_collection_journal_messages')
            # A private/group container cannot assert another channel's input.
            for message in messages:
                o=source(message)
                if o and channel!='bot_shared' and o['scope']!=channel:raise ValueError('semantic_collection_journal_source')
            yield messages,value.get('at')


def _select(items):
    """Compatible captures may extend an append-only block, never replace it."""
    complete=[m for m in items if m.get('complete') is True]
    if not complete:raise ValueError('semantic_collection_material_missing_or_incomplete')
    def raw_outputs(m):return [{k:v for k,v in x.items() if k!='owner'} for x in m.get('native_outputs',[])]
    best=max(complete,key=lambda m:(len(raw_outputs(m)),len(m.get('tools') or []),len(m.get('native_reply') or ''),m.get('version',0)))
    for m in items:
        if any(m.get(k)!=best.get(k) for k in ('text','images','subject','digest')):raise ValueError('semantic_collection_material_conflict')
        for field in ('reply','native_reply'):
            a=m.get(field) or '';b=best.get(field) or ''
            if a and b and not b.startswith(a):raise ValueError('semantic_collection_material_conflict')
        if (m.get('tools') or [])!=(best.get('tools') or [])[:len(m.get('tools') or [])]:raise ValueError('semantic_collection_material_conflict')
        outputs=raw_outputs(m)
        if outputs!=raw_outputs(best)[:len(outputs)]:raise ValueError('semantic_collection_material_conflict')
        if m.get('version',0)>=3 and best.get('version',0)>=3:
            if m.get('input_settlement') and best.get('input_settlement') and m['input_settlement']!=best['input_settlement']:raise ValueError('semantic_collection_settlement_conflict')
            for a,b in zip(m.get('native_outputs',[]),best.get('native_outputs',[])):
                if a.get('owner') and b.get('owner') and a['owner']!=b['owner']:raise ValueError('semantic_collection_owner_conflict')
    result=copy.deepcopy(best)
    if result.get('version',0)>=3:
        for m in items:
            if m.get('input_settlement') and not result.get('input_settlement'):result['input_settlement']=copy.deepcopy(m['input_settlement'])
            for old,new in zip(m.get('native_outputs',[]),result.get('native_outputs',[])):
                if old.get('owner') and not new.get('owner'):new['owner']=copy.deepcopy(old['owner'])
    replies={m['reply'] for m in items if m.get('reply')}
    if len(replies)>1:raise ValueError('semantic_collection_reply_conflict')
    if replies:result['reply']=replies.pop()
    return result


class Collected(list):
    def __init__(self,rows=(),discarded=()):
        super().__init__(rows);self.discarded=list(discarded)


def collect_local(db,scope,payload):
    """Offer unrecoverable local originals honestly at a legal checkpoint.

    The caller has already attempted Journal recapture. Keep complete captures
    on the normal load/verify path; offer remaining fragments without inventing
    a complete material row. No retirement occurs here.
    """
    if not _channel(scope):raise ValueError('semantic_collection_local_scope')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    output=Collected()
    payload=list(payload);present={str(row['event']) for row in payload}
    # A legacy Hold has a trusted scope/event even when its original and
    # author are gone. Offer that gap explicitly in this same legal batch;
    # never manufacture a sender, complete capture or independent recovery.
    if 'memory_hold_rounds' in tables:
        for event,raw in db.execute('''SELECT h.event,m.material FROM memory_hold_rounds h
          LEFT JOIN memory_round_material m ON m.scope=h.scope AND m.event=h.event
          WHERE h.scope=? ORDER BY h.created,h.event''',(scope,)):
            event=str(event)
            if event in present:continue
            if 'pipeline_scope_work' in tables and db.execute('SELECT 1 FROM pipeline_scope_work WHERE scope=? AND event=?',(scope,event)).fetchone():
                raise ValueError('semantic_batch_work_still_active')
            if 'pipeline_work_inputs' in tables and db.execute("SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND event=? AND status='owned'",(scope,event)).fetchone():
                raise ValueError('semantic_batch_input_still_owned')
            item=None
            if raw:
                try:item=json.loads(raw)
                except (TypeError,ValueError):pass
            if isinstance(item,dict):
                for key,expected in (('scope',scope),('event',event)):
                    if key in item and str(item[key])!=expected:raise ValueError('semantic_collection_identity_conflict')
                origin=source({'role':'user','content':item.get('text')})
                if origin and ((origin['scope'],str(origin['event_id']))!=(scope,event)
                    or 'subject' in item and str(origin['sender_id'])!=str(item['subject'])):
                    raise ValueError('semantic_collection_identity_conflict')
            valid=valid_material(item,scope) if isinstance(item,dict) else False
            if valid:
                payload.append({'sender_id':item['subject'],'event':event,'user':item['text'],
                    'bot':item.get('reply',''),'at':origin.get('at'),'source':origin})
                present.add(event);continue
            output.discarded.append({'event':event,'source_scope':scope,'sender_ids':[],
                'missing':'incomplete_original' if raw else 'full_original',
                'available_text':[item['text']] if isinstance(item,dict) and isinstance(item.get('text'),str) and item['text'] else [],
                'available_reply':[item['reply']] if isinstance(item,dict) and isinstance(item.get('reply'),str) and item['reply'] else [],
                'available_observations':[],'images':[]})
    for row in payload:
        event=str(row['event'])
        if 'pipeline_scope_work' in tables and db.execute('SELECT 1 FROM pipeline_scope_work WHERE scope=? AND event=?',(scope,event)).fetchone():
            raise ValueError('semantic_batch_work_still_active')
        if 'pipeline_work_inputs' in tables and db.execute("SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND event=? AND status='owned'",(scope,event)).fetchone():
            raise ValueError('semantic_batch_input_still_owned')
        saved=db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(scope,event)).fetchone()
        item=None
        if saved:
            try:item=json.loads(saved[0])
            except (TypeError,ValueError):pass
        # An unreadable format is a material gap; an explicit identity
        # disagreement is not. Check identity before schema/version fallback.
        if isinstance(item,dict):
            for key,expected in (('scope',scope),('event',event),('subject',str(row['sender_id']))):
                if key in item and (item[key] if key=='scope' else str(item[key]))!=expected:
                    raise ValueError('semantic_collection_sender_conflict' if key=='subject' else 'semantic_collection_identity_conflict')
            origin=source({'role':'user','content':item.get('text')})
            if origin and (origin['scope'],str(origin['event_id']),str(origin['sender_id']))!=(scope,event,str(row['sender_id'])):
                raise ValueError('semantic_collection_identity_conflict')
        valid=valid_material(item,scope) and str(item['event'])==event if isinstance(item,dict) else False
        if valid and item.get('complete'):
            output.append(row);continue
        images=item.get('images',[]) if valid else []
        # Pending/round rows already carry the scope-bound source and reply.
        # Invalid capture metadata must not assert another sender or media.
        output.discarded.append({'event':event,'source_scope':scope,
            'sender_ids':[str(row['sender_id'])],
            'missing':'incomplete_original' if saved else 'full_original',
            'available_text':[row['user']] if row.get('user') else [],
            'available_reply':[row['bot']] if row.get('bot') else [],
            'available_observations':[],'images':list(images)})
    return output


def unavailable_stamp(db,discarded,scope='bot_shared'):
    """CAS actual raw evidence; this hash is not model memory or coverage."""
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    values=[]
    for item in discarded:
        pair=origin(scope,item['event'])
        for logical,event in sorted({pair,(scope,item['event'])}):
            for table in ('memory_pending','memory_rounds','memory_round_material','memory_hold_rounds','memory_message_sources','pipeline_scope_work','pipeline_work_inputs',*(p[0] for p in OBSERVATION_POOLS)):
                if table in tables:values.append([table,logical,event,list(db.execute('SELECT * FROM '+table+' WHERE scope=? AND event=?',(logical,event)))])
    for table in ('fishbot_readonly_journals','memory_checkpoint_archives','fishbot_operator_session_archives'):
        if table in tables:
            rows=db.execute('SELECT scope,sha FROM '+table+' ORDER BY rowid') if scope=='bot_shared' else db.execute('SELECT scope,sha FROM '+table+' WHERE scope=? ORDER BY rowid',(model_json(scope),))
            values.append([table,list(rows)])
    return hashlib.sha256(model_json(values).encode()).hexdigest()


def collect(db,scope,cutoff,payload,recapture=None,*,discard_unavailable=False):
    """Return complete canonical inputs through cutoff; never commit or mint rounds.

    Every candidate needs real material. Unknown chronology/ownership or
    conflicting physical captures abort instead of silently losing a pool.
    Already retired source/shared identities cannot be resurrected by archives.
    """
    if scope!='bot_shared':return payload
    if not _time(cutoff):raise ValueError('semantic_collection_cutoff')
    # Releasing the outermost SQLite savepoint commits it. Keep an explicit
    # caller-owned transaction even when invoked on an otherwise idle store.
    if not db.in_transaction:db.execute('BEGIN')
    db.execute('SAVEPOINT semantic_collection')
    try:
        result=_collect(db,cutoff,payload,recapture,discard_unavailable)
        db.execute('RELEASE semantic_collection')
        return result
    except BaseException:
        db.execute('ROLLBACK TO semantic_collection');db.execute('RELEASE semantic_collection');raise


def _collect(db,cutoff,payload,recapture,discard_unavailable=False):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    retired=set()
    if 'memory_semantic_batches' in tables:
        for scope,raw in db.execute("SELECT scope,members FROM memory_semantic_batches WHERE state='retired'"):
            retired.update((scope,x) for x in json.loads(raw))
    def identity(scope,event):
        channel,identifier=origin(scope,str(event))
        if not _channel(channel):return None
        return channel,str(identifier)
    def eligible(pair):
        channel,event=pair;key=model_json([channel,event])
        if (channel,event_key(channel,event)) in retired or ('bot_shared',event_key('bot_shared',key)) in retired:return False
        for logical,identifier in ((channel,event),('bot_shared',key)):
            if 'pipeline_scope_work' in tables and db.execute('SELECT 1 FROM pipeline_scope_work WHERE scope=? AND event=?',(logical,identifier)).fetchone():return False
            if 'pipeline_work_inputs' in tables and db.execute("SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND event=? AND status='owned'",(logical,identifier)).fetchone():return False
        return True
    scopes={'bot_shared'}
    for table in ('memory_rounds','memory_pending','memory_hold_rounds','memory_round_material'):
        if table in tables:scopes.update(r[0] for r in db.execute('SELECT DISTINCT scope FROM '+table) if _channel(r[0]))
    for scope in sorted(scopes):discard_failed_service_inputs(db,scope)
    candidates={};materials={};anchors={};provided={};snippets={};raw_observations={}
    def register(pair,person=None,at=None):
        if not pair or not eligible(pair):return
        scopes.add(pair[0])
        item=candidates.setdefault(pair,{'people':set(),'times':set(),'bounds':set()})
        # Legacy/service feedback may have no author (subject=''). That is
        # missing metadata, not a second sender contradicting the actual input.
        if person is not None and (not isinstance(person,str) or person.strip()):item['people'].add(str(person))
        if _time(at):item['times'].add(at)
    for row in payload:
        pair=identity('bot_shared',row['event'])
        register(pair,row['sender_id'],row['at'])
        if pair:provided[pair]=copy.deepcopy(row)
    for table in ('memory_rounds','memory_pending'):
        if table not in tables:continue
        for scope,person,event,question,answer,at in db.execute('SELECT scope,subject,event,question,answer,at FROM '+table):
            pair=identity(scope,event);register(pair,person,at)
            if pair and eligible(pair):snippets.setdefault(pair,set()).add(question)
            if pair and answer:anchors.setdefault(pair,set()).add(answer)
    if 'memory_hold_rounds' in tables:
        for scope,event in db.execute('SELECT scope,event FROM memory_hold_rounds'):register(identity(scope,event))
    for table,person_column,text_column,time_column in OBSERVATION_POOLS:
        if table not in tables:continue
        columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
        for values in db.execute('SELECT * FROM '+table):
            row=dict(zip(columns,values));pair=identity(row['scope'],row['event'])
            register(pair,row.get(person_column),row.get(time_column))
            if not pair or not eligible(pair):continue
            if text_column and isinstance(row.get(text_column),str) and row[text_column]:
                snippets.setdefault(pair,set()).add(row[text_column])
            if table=='dialogue_feedback' and row.get('reply'):
                anchors.setdefault(pair,set()).add(row['reply'])
    def capture(item,logical,upper_bound=None):
        if not valid_material(item,logical):raise ValueError('semantic_collection_material_invalid')
        pair=identity(logical,item['event'])
        if not pair or not eligible(pair):return
        o=source({'role':'user','content':item['text']})
        register(pair,item['subject'],o.get('at'))
        if _time(upper_bound):candidates[pair]['bounds'].add(upper_bound)
        # Journals retain the original wire while saved captures have already
        # crossed the credential-removal boundary. Compare and offer the same
        # representation that remember persists, keeping immutable digests.
        mapped=material_storage_view(item);mapped.update(scope='bot_shared',event=model_json(list(pair)))
        materials.setdefault(pair,[]).append(mapped)
    def saved_materials():
        for logical,event,raw in db.execute('SELECT scope,event,material FROM memory_round_material'):
            if logical=='bot_shared' or _channel(logical):capture(json.loads(raw),logical)
    saved_materials()
    # Operator resets may archive only evidence, with no Journal at all.
    # This is real material, not a missing checksum or coverage certificate.
    if 'fishbot_operator_session_archives' in tables:
        for key,raw,sha in db.execute('SELECT scope,archive,sha FROM fishbot_operator_session_archives'):
            if hashlib.sha256(raw.encode()).hexdigest()!=sha:raise ValueError('semantic_collection_archive_integrity')
            channel=json.loads(key)
            if channel!='bot_shared' and not _channel(channel):continue
            for name,entry in json.loads(raw).get('evidence',{}).items():
                if name not in ('memory_rounds','memory_pending','memory_hold_rounds','memory_round_material'):continue
                columns=entry['columns']
                for values in entry['rows']:
                    if len(values)!=len(columns):raise ValueError('semantic_collection_archive_evidence_shape')
                    row=dict(zip(columns,values));logical=row.get('scope',channel)
                    if logical!=channel:raise ValueError('semantic_collection_archive_scope')
                    if name=='memory_round_material':capture(json.loads(row['material']),logical);continue
                    pair=identity(logical,row['event']);register(pair,row.get('subject'),row.get('at'))
                    if pair and eligible(pair) and row.get('question'):snippets.setdefault(pair,set()).add(row['question'])
                    if pair and row.get('answer'):anchors.setdefault(pair,set()).add(row['answer'])
    # Exact model bindings are platform metadata. Mirror only positively bound
    # native identities so shared recapture retains their real receipts/owners.
    if 'memory_message_sources' in tables:
        for logical,event,raw in db.execute("SELECT scope,event,data FROM memory_message_sources WHERE direction='model'").fetchall():
            if not _channel(logical):continue
            pair=identity(logical,event)
            if not pair or pair not in candidates or not eligible(pair):continue
            value=json.loads(raw)
            if (value.get('source_scope'),str(value.get('event_id')))!=(pair[0],pair[1]):raise ValueError('semantic_collection_binding_conflict')
            if str(value.get('sender_id')) not in candidates[pair]['people']:raise ValueError('semantic_collection_binding_conflict')
            key=model_json(list(pair))
            old=db.execute("SELECT data FROM memory_message_sources WHERE scope='bot_shared' AND event=? AND direction='model'",(key,)).fetchone()
            if old and json.loads(old[0])!=value:raise ValueError('semantic_collection_binding_conflict')
            if not old:db.execute("INSERT INTO memory_message_sources VALUES('bot_shared',?,'model',?)",(key,raw))
    # Discover material-only/archive-only inputs too, including silent images.
    for messages,container_at in _journals(db,tables):
        # A later timestamped input bounds preceding blocks without inventing
        # their original occurrence time. Container capture time is also real.
        upper=container_at if _time(container_at) else None;bounds={}
        for message in reversed(messages):
            o=source(message)
            if not o:continue
            if _time(o.get('at')):upper=o['at']
            if _time(upper):bounds[model_json([o['scope'],str(o['event_id'])])]=upper
        for item in snapshots(messages,'bot_shared',db=db):
            if valid_material(item,'bot_shared'):
                capture(item,'bot_shared',bounds.get(item['event']));continue
            if not discard_unavailable:raise ValueError('semantic_collection_material_invalid')
            # The verified Journal is real original material even when its
            # tool result exceeds the old bounded capture schema. Offer the
            # exact physical blocks, never truncate them to pass validation.
            pair=identity('bot_shared',item['event'])
            if not pair or not eligible(pair):continue
            register(pair,item['subject'])
            if _time(bounds.get(item['event'])):candidates[pair]['bounds'].add(bounds[item['event']])
            for index,message in enumerate(messages):
                o=source(message)
                if not o or (o['scope'],str(o['event_id']))!=pair:continue
                register(pair,str(o['sender_id']),o.get('at'))
                end=next((j for j in range(index+1,len(messages)) if messages[j].get('role')=='user'),len(messages))
                block=copy.deepcopy(messages[index:end])
                if block not in raw_observations.setdefault(pair,[]):raw_observations[pair].append(block)
    wanted=[]
    for pair,item in candidates.items():
        if not item['times'] and not item['bounds']:raise ValueError('semantic_collection_time_missing')
        if item['times'] and min(item['times'])<=cutoff<max(item['times']):raise ValueError('semantic_collection_time_conflict')
        if min(item['times'] or item['bounds'])<=cutoff:wanted.append(pair)
    if callable(recapture):
        for logical in sorted(scopes):
            events=[model_json(list(pair)) if logical=='bot_shared' else pair[1] for pair in wanted if logical=='bot_shared' or pair[0]==logical]
            if events:recapture(logical,events)
        saved_materials()
    missing=sum(not materials.get(pair) for pair in wanted)
    incomplete=sum(bool(materials.get(pair)) and not any(m.get('complete') is True for m in materials[pair]) for pair in wanted)
    if (missing or incomplete) and not discard_unavailable:raise ValueError('semantic_collection_material_missing_or_incomplete:missing='+str(missing)+',incomplete='+str(incomplete))
    output=Collected()
    def offer_unavailable(pair,captures,reason):
        texts=set(snippets.get(pair,set()))
        texts.update(m['text'] for m in captures if m.get('text'))
        raw=raw_observations.get(pair,[])
        images={image for m in captures for image in m.get('images',[])}
        for block in raw:
            for message in block:
                for part in message.get('content') if isinstance(message.get('content'),list) else []:
                    if isinstance(part,dict) and part.get('type')=='image_url':
                        url=(part.get('image_url') or {}).get('url')
                        if isinstance(url,str) and url.startswith('data:image/'):images.add(url)
        output.discarded.append({'event':model_json(list(pair)),'source_scope':pair[0],
            'sender_ids':sorted(candidates[pair]['people']),'missing':reason,
            'available_text':sorted(texts),'available_reply':sorted(anchors.get(pair,set())),
            'available_observations':raw,'images':sorted(images)})
    for pair in sorted(wanted,key=lambda p:(min(candidates[p]['times'] or candidates[p]['bounds']),p)):
        captures=materials.get(pair,[])
        if pair in raw_observations:
            offer_unavailable(pair,captures,'incomplete_original');continue
        if not any(m.get('complete') is True for m in captures):
            # Explicitly incomplete evidence never acquires a forged complete
            # material/coverage row. It is offered as available fragments only.
            offer_unavailable(pair,captures,'full_original' if not captures else 'incomplete_original')
            continue
        meta=candidates[pair]
        try:item=_select(captures)
        except ValueError:
            if not discard_unavailable:raise
            offer_unavailable(pair,captures,'conflicting_originals');continue
        if meta['people']!={item['subject']}:raise ValueError('semantic_collection_sender_conflict')
        replies={material_storage_view({'reply':reply})['reply'] for reply in anchors.get(pair,set())}
        if len(replies)>1:
            if not discard_unavailable:raise ValueError('semantic_collection_reply_conflict')
            offer_unavailable(pair,captures,'conflicting_originals');continue
        if replies:
            reply=next(iter(replies))
            if item.get('reply') and item['reply']!=reply:
                if not discard_unavailable:raise ValueError('semantic_collection_reply_conflict')
                offer_unavailable(pair,captures,'conflicting_originals');continue
            item['reply']=reply
        key=item['event'];at=min(meta['times'] or meta['bounds']);o=source({'role':'user','content':item['text']})
        row={'sender_id':item['subject'],'event':key,'user':item['text'],'bot':item.get('reply',''),'at':o.get('at') if _time(o.get('at')) else None,'observed_before':at,
             'source':{**o,'source_scope':pair[0]}}
        if row['at'] is not None:row.pop('observed_before')
        if pair in provided and str(provided[pair]['sender_id'])!=item['subject']:raise ValueError('semantic_collection_sender_conflict')
        db.execute('INSERT OR IGNORE INTO memory_pending(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)',
                   ('bot_shared',item['subject'],key,item['text'],item.get('reply',''),at))
        previous=db.execute('SELECT subject FROM memory_pending WHERE scope=? AND event=?',('bot_shared',key)).fetchone()
        if previous[0]!=item['subject']:raise ValueError('semantic_collection_sender_conflict')
        remember(db,'bot_shared',[item])
        saved=db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',('bot_shared',key)).fetchone()
        if not saved or not valid_material(json.loads(saved[0]),'bot_shared') or not json.loads(saved[0]).get('complete'):raise ValueError('semantic_collection_material_store_failed')
        # remember refuses conflicting immutable captures. Verify rather than
        # silently accepting its earlier representation as the complete batch.
        stored=json.loads(saved[0])
        if any(stored.get(k)!=item.get(k) for k in ('text','images','digest','subject','tools','native_reply','native_outputs','input_settlement','reply')):raise ValueError('semantic_collection_material_store_conflict')
        output.append(row)
    return output
