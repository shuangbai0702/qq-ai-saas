"""Lossless Journal storage codec; never changes provider message encoding.

Base is a native view, not necessarily a prefix of wire. References therefore
match the exact JSON (including object key order), with unmatched items inline.
Media references live outside user objects so arbitrary user keys cannot be
mistaken for codec instructions. Blobs contain the original UTF-8 string.
"""
import copy,hashlib,json

VERSION=2
MIN_MEDIA_CHARS=4096

def dumps(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def referenced_blobs(payload,version=VERSION):
    """Read blob references without loading the retired image bytes."""
    if version==1:return set()
    if version!=VERSION:raise ValueError('journal_snapshot_version')
    value=json.loads(payload)
    if not isinstance(value,dict) or not isinstance(value.get('media'),list):raise ValueError('journal_codec_shape')
    return {ref[1] for ref in value['media']}

def prune_unreferenced_blobs(db,scope,replacement=None,dry_run=False):
    """Collect only this scope, preserving references in explicit archives."""
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    refs=set()
    snapshots=[replacement] if replacement is not None else db.execute('SELECT version,payload FROM fishbot_readonly_journals WHERE scope=? AND payload IS NOT NULL',(scope,))
    for version,payload in snapshots:refs.update(referenced_blobs(payload,version))
    if 'memory_checkpoint_archives' in tables:
        for version,payload in db.execute('SELECT version,payload FROM memory_checkpoint_archives WHERE scope=? AND payload IS NOT NULL',(scope,)):
            refs.update(referenced_blobs(payload,version))
    if 'fishbot_operator_session_archives' in tables:
        for raw, in db.execute('SELECT archive FROM fishbot_operator_session_archives WHERE scope=?',(scope,)):
            snapshot=json.loads(raw)['journal']
            if snapshot is not None:refs.update(referenced_blobs(snapshot['payload'],snapshot['version']))
    released=0
    for digest,size in db.execute('SELECT sha,length(data) FROM fishbot_readonly_journal_blobs WHERE scope=?',(scope,)).fetchall():
        if digest not in refs:
            released+=size
            if not dry_run:db.execute('DELETE FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(scope,digest))
    return released


def retire_archived_members(db,checkpoint_id):
    """Erase successfully retired members from active and recovery copies.

    Runs inside the caller's retirement transaction. Archive authorization,
    System bytes, uncompressed events and other scoped copies stay intact.
    """
    from context_protocol import model_json
    from semantic_batch import event_key
    from readonly_journal import semantic_retire_projection
    row=db.execute("SELECT scope,members FROM memory_semantic_batches WHERE checkpoint_id=? AND state='retired'",(checkpoint_id,)).fetchone()
    if not row:return 0
    scope,members=row[0],set(json.loads(row[1]))
    permit={'semanticBatch':True,'scope':scope,'batchMembers':list(members)}
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    touched=set();changed=0

    def member(channel,event):
        if event is None:return False
        channel=channel or scope
        if channel==scope and event_key(scope,str(event)) in members:return True
        return scope=='bot_shared' and event_key(scope,model_json([channel,str(event)])) in members

    def raw_row(item,default):
        if not isinstance(item,dict):return False
        channel=item.get('source_scope',item.get('sourceScope',item.get('scope',default)))
        return any(member(channel,item.get(key)) for key in ('source_event','event_id','event'))

    def context(value,default):
        # Only platform-owned transcript fields are disposable copies.
        # Facts, summaries, execution outcomes and System contracts survive.
        if not isinstance(value,dict):return value
        value=copy.deepcopy(value)
        for key in ('recent_messages','recent_cross_session_messages','conversation_timeline','ambient_group_messages'):
            if isinstance(value.get(key),list):value[key]=[item for item in value[key] if not raw_row(item,default)]
        if isinstance(value.get('dialogue'),dict):
            for key in ('recent_own_turns','feedback','deliveries'):
                if isinstance(value['dialogue'].get(key),list):value['dialogue'][key]=[item for item in value['dialogue'][key] if not raw_row(item,default)]
        if isinstance(value.get('group_round_history'),list):
            rounds=[]
            for item in value['group_round_history']:
                if not isinstance(item,dict):rounds.append(item);continue
                item=copy.deepcopy(item);channel=item.get('source_scope',default)
                item['messages']=[message for message in item.get('messages',[]) if not raw_row(message,channel)]
                if member(channel,item.get('round_event')):
                    if not item['messages']:continue
                    item['round_event']=None;item['reply']=None
                rounds.append(item)
            value['group_round_history']=rounds
        return value

    def snapshot(version,payload,key,expected_sha=None,*,active=False):
        if expected_sha and hashlib.sha256(payload.encode()).hexdigest()!=expected_sha:raise ValueError('archive_snapshot_integrity')
        def read_blob(channel,digest):
            blob=db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(channel,digest)).fetchone()
            return blob[0] if blob else None
        value=json.loads(payload) if version==1 else decode(payload,key,read_blob)
        if value.get('scope')!=key:raise ValueError('archive_snapshot_scope')
        original=copy.deepcopy(value);default=json.loads(key)
        for field in ('base','wire'):
            value[field]=semantic_retire_projection(value[field],permit)
        value['state']=context(value.get('state'),default)
        fresh_canonical=active and default==scope and value==original
        if not fresh_canonical and (scope=='bot_shared' or default==scope or value!=original):
            # Submitted context packets are derived presentation caches, including
            # positional deltas that cannot identify a turn on their own. A legal
            # batch cutover discards these packets whole, as native cleanup does;
            # original nonmember messages and filtered state remain authoritative.
            from context_protocol import READONLY_CURRENT
            from context_transport import EVENT_CONTEXT_CURRENT
            from context_foundation import FOUNDATION_CURRENT
            prefixes=tuple(header+'\n' for header in (READONLY_CURRENT,EVENT_CONTEXT_CURRENT,FOUNDATION_CURRENT))
            for field in ('base','wire'):
                value[field]=[message for message in value[field] if not (
                    message.get('role')=='user' and isinstance(message.get('content'),str) and message['content'].startswith(prefixes))]
        if value==original:return None
        encoded,blobs=encode(value)
        for digest,raw in blobs.items():db.execute('INSERT OR IGNORE INTO fishbot_readonly_journal_blobs(scope,sha,data) VALUES(?,?,?)',(key,digest,raw))
        touched.add(key)
        return VERSION,encoded,hashlib.sha256(encoded.encode()).hexdigest()

    db.execute('SAVEPOINT semantic_archive_cleanup')
    try:
        if 'fishbot_readonly_journals' in tables:
            columns={r[1] for r in db.execute('PRAGMA table_info(fishbot_readonly_journals)')}
            checksum='sha' if 'sha' in columns else 'NULL'
            for key,version,payload,sha in db.execute('SELECT scope,version,payload,'+checksum+' FROM fishbot_readonly_journals WHERE payload IS NOT NULL').fetchall():
                update=snapshot(version,payload,key,sha,active=True)
                if update:
                    if 'sha' in columns:db.execute('UPDATE fishbot_readonly_journals SET version=?,payload=?,sha=? WHERE scope=?',(*update,key))
                    else:db.execute('UPDATE fishbot_readonly_journals SET version=?,payload=? WHERE scope=?',(*update[:2],key))
                    changed+=1
        if 'memory_checkpoint_archives' in tables:
            columns={r[1] for r in db.execute('PRAGMA table_info(memory_checkpoint_archives)')}
            checksum='sha' if 'sha' in columns else 'NULL'
            for identifier,key,version,payload,sha in db.execute('SELECT rowid,scope,version,payload,'+checksum+' FROM memory_checkpoint_archives WHERE payload IS NOT NULL').fetchall():
                update=snapshot(version,payload,key,sha)
                if update:
                    if 'sha' in columns:db.execute('UPDATE memory_checkpoint_archives SET version=?,payload=?,sha=? WHERE rowid=?',(*update,identifier))
                    else:db.execute('UPDATE memory_checkpoint_archives SET version=?,payload=? WHERE rowid=?',(*update[:2],identifier))
                    changed+=1
        if 'fishbot_operator_session_archives' in tables:
            updates=[]
            columns={r[1] for r in db.execute('PRAGMA table_info(fishbot_operator_session_archives)')}
            created='created' if 'created' in columns else 'NULL'
            grant=None
            if scope=='bot_shared' and 'memory_semantic_discard_audit' in tables:
                grant=db.execute('SELECT b.cutoff FROM memory_semantic_batches b JOIN memory_semantic_discard_audit a ON a.checkpoint_id=b.checkpoint_id WHERE b.checkpoint_id=? AND length(a.administrator)>0 AND length(a.request_id)>0',(checkpoint_id,)).fetchone()
            for identifier,key,encoded,sha,captured in db.execute('SELECT authorization,scope,archive,sha,'+created+' FROM fishbot_operator_session_archives').fetchall():
                if hashlib.sha256(encoded.encode()).hexdigest()!=sha:raise ValueError('operator_archive_integrity')
                archive=json.loads(encoded);original=copy.deepcopy(archive);journal=archive['journal']
                update=snapshot(journal['version'],journal['payload'],key,journal.get('sha')) if journal is not None else None
                if update:journal.update(zip(('version','payload','sha'),update))
                # Legacy operator snapshots can contain native SDK history or
                # unbound messages that no longer have an event identity. Only
                # an explicit administrator loss grant permits discarding this
                # old diagnostic copy whole; ordinary retirement stays exact.
                discard_legacy=bool(grant and type(captured) in (int,float) and 0<captured<=grant[0])
                if discard_legacy:
                    channel=json.loads(key)
                    for logical in {scope,channel}:
                        if 'pipeline_scope_work' in tables and db.execute('SELECT 1 FROM pipeline_scope_work WHERE scope=?',(logical,)).fetchone():raise ValueError('semantic_archive_work_still_active')
                        if 'pipeline_work_inputs' in tables and db.execute("SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND status='owned'",(logical,)).fetchone():raise ValueError('semantic_archive_input_still_owned')
                    if journal is not None:
                        def blob(channel,digest):
                            row=db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(channel,digest)).fetchone()
                            return row[0] if row else None
                        value=json.loads(journal['payload']) if journal['version']==1 else decode(journal['payload'],key,blob)
                        for field in ('base','wire'):value[field]=[m for m in value[field] if m.get('role') in ('system','developer')]
                        value['state']={}
                        payload,blobs=encode(value)
                        for digest,raw in blobs.items():db.execute('INSERT OR IGNORE INTO fishbot_readonly_journal_blobs(scope,sha,data) VALUES(?,?,?)',(key,digest,raw))
                        journal.update(version=VERSION,payload=payload,sha=hashlib.sha256(payload.encode()).hexdigest())
                    archive.pop('sdkConversations',None);archive.pop('sdkPreference',None)
                    archive['retirement']={'checkpoint':checkpoint_id,'mode':'administrator_discard_unavailable'}
                for name,evidence in archive.get('evidence',{}).items():
                    if name not in ('memory_rounds','memory_round_material','memory_pending','memory_pending_rounds','memory_hold_rounds',
                                    'memory_summary_coverage','memory_summary_unresolved','memory_media_retirement_choices','memory_message_sources','dialogue_feedback'):continue
                    if discard_legacy:
                        evidence['rows']=[];continue
                    columns=evidence['columns']
                    if 'event' not in columns:continue
                    event_index=columns.index('event');scope_index=columns.index('scope') if 'scope' in columns else None
                    evidence['rows']=[record for record in evidence['rows'] if not member(record[scope_index] if scope_index is not None else json.loads(key),record[event_index])]
                if archive!=original:
                    replacement=dumps(archive);updates.append((replacement,hashlib.sha256(replacement.encode()).hexdigest(),identifier));touched.add(key)
            if updates:
                triggers=db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='fishbot_operator_session_archives'").fetchall()
                for name,sql in triggers:db.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
                db.executemany('UPDATE fishbot_operator_session_archives SET archive=?,sha=? WHERE authorization=?',updates)
                for name,sql in triggers:db.execute(sql)
                changed+=len(updates)
        for key in touched:prune_unreferenced_blobs(db,key)
        db.execute('RELEASE semantic_archive_cleanup')
        return changed
    except BaseException:
        db.execute('ROLLBACK TO semantic_archive_cleanup');db.execute('RELEASE semantic_archive_cleanup');raise

def read_snapshot(db,scope):
    """Read a committed snapshot without creating tables or rewriting data."""
    row=db.execute('SELECT version,payload,sha FROM fishbot_readonly_journals WHERE scope=?',(scope,)).fetchone()
    if not row or row[1] is None:return None
    version,payload,sha=row
    if version not in (1,VERSION) or not isinstance(payload,str) or hashlib.sha256(payload.encode('utf-8')).hexdigest()!=sha:raise ValueError('journal_snapshot_integrity')
    def read_blob(key,digest):
        blob=db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(key,digest)).fetchone()
        return blob[0] if blob else None
    value=json.loads(payload) if version==1 else decode(payload,scope,read_blob)
    if not isinstance(value,dict) or value.get('scope')!=scope or not isinstance(value.get('base'),list):raise ValueError('journal_snapshot_invalid')
    if any(not isinstance(m,dict) or not isinstance(m.get('role'),str) for m in value['base']):raise ValueError('journal_snapshot_invalid')
    return value

def encode(value):
    value=copy.deepcopy(value)
    indices={}
    for i,message in enumerate(value['wire']):indices.setdefault(dumps(message),i)
    value['base']=[{'wire':indices[dumps(m)]} if dumps(m) in indices else {'inline':m} for m in value['base']]
    refs=[];blobs={}
    def visit(node,path):
        if isinstance(node,str) and len(node)>=MIN_MEDIA_CHARS and node.startswith('data:') and node.split(',',1)[0].endswith(';base64'):
            raw=node.encode('utf-8');digest=hashlib.sha256(raw).hexdigest()
            blobs[digest]=raw;refs.append([path,digest,len(raw)])
            return None
        if isinstance(node,dict):return {k:visit(v,path+[k]) for k,v in node.items()}
        if isinstance(node,list):return [visit(v,path+[i]) for i,v in enumerate(node)]
        return node
    return dumps({'value':visit(value,[]),'media':refs}),blobs

def decode(payload,scope,read_blob):
    envelope=json.loads(payload)
    if not isinstance(envelope,dict) or set(envelope)!={'value','media'} or not isinstance(envelope['value'],dict) or not isinstance(envelope['media'],list):raise ValueError('journal_codec_shape')
    value=envelope['value']
    if value.get('scope')!=scope:raise ValueError('journal_codec_scope')
    seen=set()
    for ref in envelope['media']:
        if not isinstance(ref,list) or len(ref)!=3:raise ValueError('journal_blob_reference')
        path,digest,size=ref
        if not isinstance(path,list) or not path or any(type(k) not in (str,int) for k in path) or not isinstance(digest,str) or len(digest)!=64 or type(size) is not int or size<0:raise ValueError('journal_blob_reference')
        key=tuple(path)
        if key in seen:raise ValueError('journal_blob_duplicate')
        seen.add(key);parent=value
        for part in path[:-1]:parent=parent[part]
        if parent[path[-1]] is not None:raise ValueError('journal_blob_placeholder')
        raw=read_blob(scope,digest)
        if not isinstance(raw,bytes) or len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('journal_blob_integrity')
        parent[path[-1]]=raw.decode('utf-8')
    wire=value.get('wire');base=value.get('base')
    if not isinstance(wire,list) or not isinstance(base,list):raise ValueError('journal_codec_messages')
    native=[]
    for ref in base:
        if not isinstance(ref,dict):raise ValueError('journal_base_reference')
        if set(ref)=={'wire'}:
            index=ref['wire']
            if type(index) is not int or index<0 or index>=len(wire):raise ValueError('journal_base_index')
            native.append(copy.deepcopy(wire[index]))
        elif set(ref)=={'inline'}:native.append(ref['inline'])
        else:raise ValueError('journal_base_reference')
    value['base']=native
    return value
