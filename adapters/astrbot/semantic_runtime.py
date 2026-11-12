"""Finish committed semantic checkpoints without waiting for another message."""
import copy,json,sqlite3,time,uuid
from dataclasses import replace
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import semantic_batch
from context_protocol import model_json
from readonly_journal import ReadonlyJournal,semantic_retire_projection


def _retired_members(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_semantic_batches'").fetchone():return {}
    result={}
    for scope,raw in db.execute("SELECT scope,members FROM memory_semantic_batches WHERE state='retired'"):
        result.setdefault(scope,set()).update(json.loads(raw))
    return result


def _retired_row(row,default_scope,members,event_field=None):
    if not isinstance(row,dict):return False
    event=row.get(event_field) if event_field else row.get('source_event',row.get('event_id'))
    if event is None:return False
    scope=row.get('source_scope',row.get('sourceScope',row.get('scope',default_scope)))
    if semantic_batch.event_key(scope,event) in members.get(scope,set()):return True
    shared_event=str(event) if scope=='bot_shared' else model_json([scope,str(event)])
    return semantic_batch.event_key('bot_shared',shared_event) in members.get('bot_shared',set())


def filter_retired_context(st,scope,value):
    """Filter hot transcript windows only; derived semantic products survive."""
    members=_retired_members(st.db)
    if not members:return value
    result=dict(value)
    for field in ('recent_messages','recent_cross_session_messages','conversation_timeline','ambient_group_messages'):
        if isinstance(result.get(field),list):result[field]=[row for row in result[field] if not _retired_row(row,scope,members)]
    if isinstance(result.get('dialogue'),dict):
        result['dialogue']=dict(result['dialogue'])
        for field in ('recent_own_turns','feedback','deliveries'):
            if isinstance(result['dialogue'].get(field),list):result['dialogue'][field]=[row for row in result['dialogue'][field] if not _retired_row(row,scope,members)]
    if isinstance(result.get('group_round_history'),list):
        rounds=[]
        for original in result['group_round_history']:
            row=dict(original);origin=row.get('source_scope',scope)
            row['messages']=[message for message in row.get('messages',[]) if not _retired_row(message,origin,members)]
            if _retired_row(row,scope,members,'round_event'):
                if not row['messages']:continue
                row['round_event']=None;row['reply']=None
            rounds.append(row)
        result['group_round_history']=rounds
    return result


def _prune_hot_memory(st):
    members=_retired_members(st.db)
    ambient=getattr(getattr(st,'ambient',None),'scopes',{})
    for scope,rows in list(ambient.items()):
        kept=[row for row in rows if not _retired_row(row,scope,members)]
        rows.clear();rows.extend(kept)
        if not rows:ambient.pop(scope,None)
    for scope,state in getattr(getattr(st,'participation',None),'scopes',{}).items():
        if isinstance(state.get('turns'),list):state['turns']=[row for row in state['turns'] if not _retired_row(row,scope,members)]


def _execution_current(previous):
    value=previous.get('execution_experience')
    if isinstance(value,dict) and value.get('basis')=='memory_foundation.execution_experience':
        from decision_history import apply_changes
        value=apply_changes(previous.get('memory_foundation',{}).get('execution_experience',{}),value.get('changes',[]))
    return copy.deepcopy(value)


def _fresh_state(st,cfg,scope,checkpoint,journal):
    """Read a post-retirement view, rolling back the temporary SQL preview."""
    import bot_pipeline,execution_memory
    subject=scope[8:] if scope.startswith('private:') else str(cfg.get('ownerUin') or '0')
    st.db.execute('SAVEPOINT semantic_state_preview')
    previous=st.model_journals.get(scope)
    try:
        st.db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=?",(checkpoint,))
        semantic_batch.purge(st.db,checkpoint)
        permit={**semantic_batch.permit(st.db,checkpoint),'scope':scope}
        st.model_journals[scope]=SimpleNamespace(last=replace(journal.last,base=semantic_retire_projection(journal.last.base,permit)))
        shared=bot_pipeline.conversation_context(st,cfg,scope,subject,'',time.time())
        # Never use the old Journal fallback for summaries at a legal cutover.
        from memory_reflection import summary_foundation
        shared['memory_foundation']['conversation_summaries']=summary_foundation(st.db,scope,str(cfg.get('ownerUin') or ''))
        from conversation_continuity import view as continuity_view
        continuity=continuity_view(st.db,scope,checkpoint_id=checkpoint)
        shared['memory_foundation'].pop('conversation_continuity',None)
        if continuity:shared['memory_foundation']['conversation_continuity']=continuity
        execution=_execution_current(journal.last.state)
        if execution is not None:shared['execution_experience']=execution_memory.context_projection(execution,shared['memory_foundation'])
        return copy.deepcopy(filter_retired_context(st,scope,shared))
    finally:
        if previous is None:st.model_journals.pop(scope,None)
        else:st.model_journals[scope]=previous
        st.db.execute('ROLLBACK TO semantic_state_preview');st.db.execute('RELEASE semantic_state_preview')


def _issue(st,scope,checkpoint,reason,resolved=False):
    with st.db:st.reflection.checkpoint_issue(scope,checkpoint,'semantic_retirement',{'result':reason},resolved=resolved)


def _journals_busy(st):
    # A staged candidate is the provider's durable, possibly submitted wire.
    # Its owner must settle it before any all-scope physical projection.
    return any(getattr(journal,'db',None) is st.db and getattr(journal,'_staged',None) is not None
               for journal in st.model_journals.values())


def _refresh_projected_journals(st):
    """Adopt committed disk projections and fence any unstaged old candidate."""
    import journal_storage
    updates=[]
    for journal in st.model_journals.values():
        if getattr(journal,'db',None) is not st.db or journal.last is None:continue
        if journal._staged is not None:raise ValueError('semantic_journal_still_staged')
        value=journal_storage.read_snapshot(st.db,journal.scope_key)
        if value is None:continue
        fields={name:copy.deepcopy(value[name]) for name in ('base','wire','state','turns','at','group_turn','base_chars','detail','usage','authority','retirement')}
        if fields['group_turn'] is not None:fields['group_turn']=tuple(fields['group_turn'])
        # Build every replacement before changing memory, so malformed disk
        # evidence cannot leave only a subset of cached scopes refreshed.
        updates.append((journal,replace(journal.last,serial=journal.serial+1,**fields)))
    for journal,prepared in updates:
        journal.serial+=1;journal.last=prepared


def finish_semantic_batches(st,cfg,scope):
    """Synchronous owner callback: reserve, replace, purge, retry safely."""
    if st.db.in_transaction:return {'result':'transaction_busy'}
    semantic_batch.initialize(st.db)
    rows=st.db.execute('''SELECT b.checkpoint_id,b.state,c.state,c.summary_id FROM memory_semantic_batches b
      JOIN memory_reconstruction_checkpoints c ON c.id=b.checkpoint_id WHERE b.scope=? ORDER BY b.checkpoint_id''',(scope,)).fetchall()
    result={'retired':0,'pending':0}
    for checkpoint,batch_state,checkpoint_state,summary_id in rows:
        if _journals_busy(st):
            _issue(st,scope,checkpoint,'journal_busy_or_unavailable');result['pending']+=1;continue
        if batch_state=='retired':
            with st.db:semantic_batch.purge(st.db,checkpoint)
            _refresh_projected_journals(st)
            continue
        if checkpoint_state!='available':result['pending']+=1;continue
        journal=st.model_journals.get(scope)
        if journal is None:
            journal=ReadonlyJournal(st.db,scope)
            if journal.last is not None:st.model_journals[scope]=journal
        if journal.last is None or journal._staged is not None or journal.recovery_error or journal.retired_reason:
            _issue(st,scope,checkpoint,'journal_busy_or_unavailable');result['pending']+=1;continue
        permit=None
        try:
            state=_fresh_state(st,cfg,scope,checkpoint,journal)
            permit=st.reflection.reserve_context_reconstruction(scope,'semantic-retire:'+uuid.uuid4().hex,summary_ids=[summary_id],checkpoint_id=checkpoint)
            if not permit or permit.get('id')!=checkpoint or not permit.get('semanticBatch'):
                if permit:st.reflection.settle_context_reconstruction(permit,False)
                _issue(st,scope,checkpoint,'checkpoint_not_reservable');result['pending']+=1;continue
            if not journal.retire_semantic_batch(permit,st.reflection,state,retirement=st.reflection.reconstruction_stamp(scope)):
                st.reflection.settle_context_reconstruction(permit,False)
                _issue(st,scope,checkpoint,'journal_replace_refused');result['pending']+=1;continue
            with st.db:semantic_batch.purge(st.db,checkpoint)
            _refresh_projected_journals(st)
            _issue(st,scope,checkpoint,'retired',resolved=True);result['retired']+=1
        except Exception as error:
            # No HTTP exists on this path. Refund only a still-reserved permit;
            # consumed permits remain bound to their durable replacement.
            if permit:
                row=st.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE id=?',(permit['id'],)).fetchone()
                if row and row[0]=='reserved':st.reflection.settle_context_reconstruction(permit,False)
            _issue(st,scope,checkpoint,'error:'+type(error).__name__);result['pending']+=1
    import semantic_media
    _prune_hot_memory(st)
    result['media']=semantic_media.drain(st.db)
    native=getattr(st,'semantic_native',None)
    if native:
        legacy=None
        if st.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_semantic_discard_audit'").fetchone():
            legacy=st.db.execute("SELECT b.checkpoint_id FROM memory_semantic_batches b JOIN memory_semantic_discard_audit a ON a.checkpoint_id=b.checkpoint_id WHERE b.scope='bot_shared' AND b.state='retired' ORDER BY b.cutoff DESC LIMIT 1").fetchone()
            if legacy and st.db.execute("SELECT 1 FROM memory_checkpoint_issues WHERE scope='bot_shared' AND checkpoint_id=? AND kind='native_legacy_cleanup' AND status='resolved'",(legacy[0],)).fetchone():legacy=None
        try:result['native']=clean_native_history(native[0],native[1],st,reset_legacy=bool(legacy))
        except (OSError,sqlite3.Error,ValueError,TypeError) as error:result['native']={'result':'pending','reason':type(error).__name__}
        if legacy:
            with st.db:st.reflection.checkpoint_issue('bot_shared',legacy[0],'native_legacy_cleanup',result['native'],resolved=result['native'].get('result')=='clean')
        with st.db:
            for checkpoint,native_scope in st.db.execute("SELECT checkpoint_id,scope FROM memory_semantic_batches WHERE state='retired'").fetchall():
                st.reflection.checkpoint_issue(native_scope,checkpoint,'native_history_cleanup',result['native'],resolved=result['native'].get('result')=='clean')
    st.semantic_retirement_status=result
    if result['retired']:
        print('FISHBOT_SEMANTIC_RETIREMENT '+model_json({'scope':scope,**result}),flush=True)
    return result


def clean_native_history(manager,platform_id,st,cid=None,*,reset_legacy=False):
    """Update the pinned SDK's actual SQLite rows under one short write lock."""
    path=getattr(getattr(manager,'db',None),'db_path',None)
    if not path or not Path(path).is_file():return {'result':'unavailable','reason':'native_sqlite_not_configured'}
    permits=[]
    for scope,members in st.db.execute("SELECT scope,members FROM memory_semantic_batches WHERE state='retired'"):
        permits.append({'scope':scope,'semanticBatch':True,'batchMembers':json.loads(members)})
    if not permits:return {'result':'clean','conversations':0}
    grant=None
    tables={r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if reset_legacy and getattr(st,'shared_context',False) and 'memory_semantic_discard_audit' in tables:
        grant=st.db.execute("SELECT max(b.cutoff) FROM memory_semantic_batches b JOIN memory_semantic_discard_audit a ON a.checkpoint_id=b.checkpoint_id WHERE b.scope='bot_shared' AND b.state='retired' AND length(a.administrator)>0 AND length(a.request_id)>0").fetchone()[0]
    if grant is not None:
        # This is a derived SDK cache reset at a committed shared-Journal
        # cutover, not deletion of the authoritative new-batch Journal. Never
        # race an active tool chain or a request staged against that cache.
        if _journals_busy(st):return {'result':'pending','reason':'native_journal_busy'}
        if 'pipeline_scope_work' in tables and st.db.execute('SELECT 1 FROM pipeline_scope_work').fetchone():return {'result':'pending','reason':'native_work_active'}
        if 'pipeline_work_inputs' in tables and st.db.execute("SELECT 1 FROM pipeline_work_inputs WHERE status='owned'").fetchone():return {'result':'pending','reason':'native_input_owned'}
    changed=0;logged=0;unresolved=0
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=rw',uri=True,timeout=0.1)) as native, native:
        columns={r[1] for r in native.execute('PRAGMA table_info(conversations)')}
        if not {'conversation_id','platform_id','content','token_usage'}<=columns:return {'result':'unavailable','reason':'native_schema_mismatch'}
        native.execute('PRAGMA secure_delete=ON');native.execute('BEGIN IMMEDIATE')
        query='SELECT conversation_id,content FROM conversations WHERE platform_id=?';args=[platform_id]
        if cid is not None:query+=' AND conversation_id=?';args.append(cid)
        for identifier,raw in native.execute(query,args).fetchall():
            if raw is None:continue
            history=json.loads(raw)
            if not isinstance(history,list):raise ValueError('native_history_shape')
            projected=history
            for permit in permits:projected=semantic_retire_projection(projected,permit)
            if grant is not None:projected=[m for m in projected if m.get('role') in ('system','developer')]
            if projected!=history:
                # SDK copies of readonly snapshots are disposable at a legal
                # checkpoint; the new canonical Journal owns the fresh view.
                from context_protocol import READONLY_CURRENT
                from context_transport import EVENT_CONTEXT_CURRENT
                from context_foundation import FOUNDATION_CURRENT
                projected=[m for m in projected if not (m.get('role')=='user' and isinstance(m.get('content'),str) and m['content'].startswith((READONLY_CURRENT+'\n',EVENT_CONTEXT_CURRENT+'\n',FOUNDATION_CURRENT+'\n')))]
                native.execute('UPDATE conversations SET content=?,token_usage=0 WHERE conversation_id=? AND platform_id=?',(model_json(projected),identifier,platform_id));changed+=1
        columns={r[1] for r in native.execute('PRAGMA table_info(platform_message_history)')}
        if columns:
            if not {'platform_id','content'}<=columns:raise ValueError('native_message_history_schema_mismatch')
            members=_retired_members(st.db)
            for identifier,raw in native.execute('SELECT rowid,content FROM platform_message_history WHERE platform_id=?',(platform_id,)).fetchall():
                source=_native_source(raw)
                if source is None:
                    # Plain text, timestamps and display names do not prove event ownership.
                    unresolved+=1
                elif _retired_row(source,source['scope'],members):
                    logged+=native.execute('DELETE FROM platform_message_history WHERE rowid=? AND platform_id=?',(identifier,platform_id)).rowcount
    return {'result':'pending' if unresolved else 'clean','conversations':changed,'legacyReset':grant is not None,'historyRows':logged,
            'unresolvedHistoryRows':unresolved,**({'reason':'native_history_source_unproven'} if unresolved else {})}


def _native_source(content):
    """SDK metadata is written from the platform event/Work, never message text."""
    if isinstance(content,str):
        try:content=json.loads(content)
        except (ValueError,TypeError):return None
    value=content.get('_fishbot_source') if isinstance(content,dict) else None
    if not isinstance(value,dict):return None
    scope=value.get('scope');event=value.get('event_id')
    if not isinstance(scope,str) or not scope.startswith(('private:','group:')) or not isinstance(event,str) or not event:return None
    return {'scope':scope,'event_id':event}


def install_native_retirement_guard(manager,stores,settings):
    """Fence actual SDK commits and reads against the existing retired batches.

    The SDK and Journal use different databases: the post-await read fence is
    mandatory even when physical deletion fails. No second retirement ledger.
    """
    db=getattr(manager,'db',None)
    if db is None or getattr(db,'_semantic_retirement_guard',False):return
    def owner(platform):
        st=stores.get(platform)
        return st if manager.db is db and st is not None and platform in settings and not getattr(st.reflection,'closed',False) else None
    def cleanup(platform,st,cid=None):
        try:status=clean_native_history(manager,platform,st,cid)
        except (OSError,sqlite3.Error,ValueError,TypeError) as error:status={'result':'pending','reason':type(error).__name__}
        st.semantic_native_status=status
        return status
    if hasattr(db,'update_conversation'):
        original=db.update_conversation
        async def update(*args,**kwargs):
            value=await original(*args,**kwargs)
            platform=getattr(value,'platform_id',None);st=owner(platform)
            if st is not None:cleanup(platform,st,getattr(value,'conversation_id',None))
            return value
        db.update_conversation=update
    if hasattr(db,'insert_platform_message_history'):
        original_insert=db.insert_platform_message_history
        async def insert(*args,**kwargs):
            platform=kwargs.get('platform_id',args[0] if args else None);st=owner(platform)
            content=kwargs.get('content',args[2] if len(args)>2 else None)
            if st is not None and isinstance(content,dict):
                # The pinned SDK binds this ContextVar around real pipeline/hook
                # execution. Metadata supplied by a caller is never authority.
                content=dict(content);content.pop('_fishbot_source',None)
                from astrbot.core.platform.astr_message_event import current_message_event
                event=current_message_event.get()
                user=kwargs.get('user_id',args[1] if len(args)>1 else None)
                if event is not None and event.get_platform_id()==platform and event.unified_msg_origin==user:
                    work=event.get_extra('_hosted_pipeline') if content.get('type')=='bot' else None
                    scope=getattr(work,'scope',None) or ('group:'+str(event.get_group_id()) if event.get_group_id() else 'private:'+str(event.get_sender_id()))
                    identifier=getattr(work,'event_id',None) or getattr(event.message_obj,'message_id',None)
                    if identifier is not None:content['_fishbot_source']={'scope':scope,'event_id':str(identifier)}
                if 'content' in kwargs:kwargs={**kwargs,'content':content}
                elif len(args)>2:args=(*args[:2],content,*args[3:])
            source=_native_source(content)
            if st is not None and _retired_members(st.db):
                if source is None:
                    st.semantic_native_status={'result':'pending','reason':'native_history_source_unproven'}
                    raise ValueError('native_history_source_unproven')
                if _retired_row(source,source['scope'],_retired_members(st.db)):return None
            value=await original_insert(*args,**kwargs)
            # Retirement can commit while the asynchronous insert is in flight.
            st=owner(platform)
            if st is not None:
                status=cleanup(platform,st)
                if source and _retired_row(source,source['scope'],_retired_members(st.db)):
                    if status.get('result')!='clean':raise ValueError('native_history_cleanup_pending')
                    return None
            return value
        db.insert_platform_message_history=insert
    if hasattr(db,'get_platform_message_history'):
        original_get=db.get_platform_message_history
        async def get(*args,**kwargs):
            platform=kwargs.get('platform_id',args[0] if args else None)
            st=owner(platform);lease=settings.get(platform)
            rows=await original_get(*args,**kwargs)
            # A late SDK read belongs to the store/configuration that began it.
            # Never reinterpret its rows under a replacement tenant binding or
            # return them unfiltered after the original owner has been released.
            if owner(platform) is not st or settings.get(platform) is not lease:return []
            if st is None:return rows
            members=_retired_members(st.db)
            if not members:return rows
            result=[]
            for row in rows:
                source=_native_source(getattr(row,'content',None))
                if source is None:
                    st.semantic_native_status={'result':'pending','reason':'native_history_source_unproven'}
                elif not _retired_row(source,source['scope'],members):result.append(row)
            return result
        db.get_platform_message_history=get
    db._semantic_retirement_guard=True
