"""Finish committed semantic checkpoints without waiting for another message."""
import copy,json,sqlite3,time,uuid
from dataclasses import replace
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import semantic_batch
from context_protocol import model_json
from input_reconciliation import filter_retired_context,retired_row as _retired_row
from readonly_journal import ReadonlyJournal,semantic_retire_projection


def _retired_members(db):
    return semantic_batch.retired_members(db)


def _prune_hot_memory(st):
    members=_retired_members(st.db)
    ambient=getattr(getattr(st,'ambient',None),'scopes',{})
    for scope,rows in list(ambient.items()):
        kept=[row for row in rows if not _retired_row(row,scope,members)]
        rows.clear();rows.extend(kept)
        if not rows:ambient.pop(scope,None)
    for scope,state in getattr(getattr(st,'participation',None),'scopes',{}).items():
        if isinstance(state.get('turns'),list):state['turns']=[row for row in state['turns'] if not _retired_row(row,scope,members)]


def _fresh_state(st,cfg,scope,checkpoint,journal):
    """Read a post-retirement view, rolling back the temporary SQL preview."""
    import bot_pipeline
    subject=scope[8:] if scope.startswith('private:') else str(cfg.get('ownerUin') or '0')
    st.db.execute('SAVEPOINT semantic_state_preview')
    previous=st.model_journals.get(scope)
    try:
        st.db.execute("UPDATE memory_semantic_batches SET state='retired' WHERE checkpoint_id=?",(checkpoint,))
        semantic_batch.purge(st.db,checkpoint,preview=True)
        permit={**semantic_batch.permit(st.db,checkpoint),'scope':scope}
        st.model_journals[scope]=SimpleNamespace(last=replace(journal.last,base=semantic_retire_projection(journal.last.base,permit)))
        shared=bot_pipeline.conversation_context(st,cfg,scope,subject,'',time.time())
        # Only this lawful reconstruction retires legacy ledger copies from
        # the frozen cognitive basis. Ordinary appends keep old wire intact.
        for field in ('completed_experience','execution_experience'):
            shared['memory_foundation'].pop(field,None)
        # Never use the old Journal fallback for summaries at a legal cutover.
        from memory_reflection import summary_foundation
        shared['memory_foundation']['conversation_summaries']=summary_foundation(st.db,scope,str(cfg.get('ownerUin') or ''))
        from conversation_continuity import view as continuity_view
        continuity=continuity_view(st.db,scope,checkpoint_id=checkpoint)
        shared['memory_foundation'].pop('conversation_continuity',None)
        if continuity:shared['memory_foundation']['conversation_continuity']=continuity
        return copy.deepcopy(filter_retired_context(st,scope,shared))
    finally:
        if previous is None:st.model_journals.pop(scope,None)
        else:st.model_journals[scope]=previous
        st.db.execute('ROLLBACK TO semantic_state_preview');st.db.execute('RELEASE semantic_state_preview')


def _issue(st,scope,checkpoint,reason,resolved=False):
    with st.db:st.reflection.checkpoint_issue(scope,checkpoint,'semantic_retirement',reason if isinstance(reason,dict) else {'result':reason},resolved=resolved)


def _journals_busy(st):
    # A staged candidate is the provider's durable, possibly submitted wire.
    # Its owner must settle it before any all-scope physical projection.
    return any(getattr(journal,'db',None) is st.db and getattr(journal,'_staged',None) is not None
               for journal in st.model_journals.values())


def finish_semantic_batches(st,cfg,scope):
    """Synchronous owner callback: reserve, replace, purge, retry safely."""
    if st.db.in_transaction:return {'result':'transaction_busy'}
    semantic_batch.initialize(st.db)
    st.db.commit()
    rows=st.db.execute('''SELECT b.checkpoint_id,b.state,c.state,c.summary_id FROM memory_semantic_batches b
      JOIN memory_reconstruction_checkpoints c ON c.id=b.checkpoint_id WHERE b.scope=?
      AND NOT EXISTS (SELECT 1 FROM memory_semantic_purges p WHERE p.checkpoint_id=b.checkpoint_id AND p.cleanup_version>=?)
      ORDER BY b.checkpoint_id''',(scope,semantic_batch.PURGE_VERSION)).fetchall()
    result={'retired':0,'pending':0}
    for checkpoint,batch_state,checkpoint_state,summary_id in rows:
        if _journals_busy(st):
            _issue(st,scope,checkpoint,'journal_busy_or_unavailable');result['pending']+=1;continue
        if batch_state=='retired':
            try:
                with st.db:semantic_batch.purge(st.db,checkpoint)
                _issue(st,scope,checkpoint,'retired',resolved=True)
            except (ValueError,sqlite3.Error) as error:
                _issue(st,scope,checkpoint,str(error) if isinstance(error,ValueError) else 'error:'+type(error).__name__);result['pending']+=1
            continue
        if checkpoint_state=='reserved' and batch_state=='committed':
            # This exact local-only path has no HTTP between reservation and
            # stage. Stage atomically consumes the permit with Journal+batch;
            # a still-reserved local permit therefore cannot have been staged.
            # Generic (possibly submitted) model permits are never refunded.
            with st.db:
                changed=st.db.execute("UPDATE memory_reconstruction_checkpoints SET state='available',request_id=NULL WHERE id=? AND scope=? AND state='reserved' AND request_id LIKE 'semantic-retire:%' AND EXISTS(SELECT 1 FROM memory_semantic_batches b WHERE b.checkpoint_id=memory_reconstruction_checkpoints.id AND b.state='committed')",(checkpoint,scope)).rowcount
            if changed:checkpoint_state='available'
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
            _issue(st,scope,checkpoint,'retired',resolved=True);result['retired']+=1
        except Exception as error:
            # No HTTP exists on this path. Refund only a still-reserved permit;
            # consumed permits remain bound to their durable replacement.
            if permit:
                row=st.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE id=?',(permit['id'],)).fetchone()
                if row and row[0]=='reserved':st.reflection.settle_context_reconstruction(permit,False)
            from readonly_journal import ContextCapacityExceeded
            reason=({'result':'reconstruction_capacity_exceeded','capacity':error.capacity,'automatic_recovery':'local_retry_without_model','history_unchanged':True}
                    if isinstance(error,ContextCapacityExceeded)
                    else str(error) if str(error) in ('semantic_active_journal_reconstruction_required','semantic_batch_input_responsibility_pending') else 'error:'+type(error).__name__)
            _issue(st,scope,checkpoint,reason);result['pending']+=1
    if scope=='bot_shared' and semantic_batch.owner_cleanup_pending(st.db):
        stamp=semantic_batch.owner_cleanup_stamp(st.db)
        if _journals_busy(st):
            with st.db:st.reflection.checkpoint_issue(scope,0,'owner_hot_cleanup',{'result':'journal_busy_or_unavailable','membersSha':stamp})
            result['pending']+=1
        else:
            try:
                with st.db:
                    semantic_batch.purge_owner_history(st.db)
                    st.reflection.checkpoint_issue(scope,0,'owner_hot_cleanup',{'membersSha':stamp,'cleanupVersion':semantic_batch.PURGE_VERSION},resolved=True)
            except (ValueError,sqlite3.Error) as error:
                with st.db:st.reflection.checkpoint_issue(scope,0,'owner_hot_cleanup',{'result':str(error) if isinstance(error,ValueError) else type(error).__name__,'membersSha':stamp})
                result['pending']+=1
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
    pipeline=getattr(st,'pipeline',None)
    if pipeline and not result['pending']:
        pipeline.resume_compression_inputs(scope)
        if result['retired']:
            with st.db:st.reflection.checkpoint_issue(scope,0,'capacity_before_checkpoint',{'recoveredByCheckpoint':True},resolved=True)
    if result['retired']:
        print('FISHBOT_SEMANTIC_RETIREMENT '+model_json({'scope':scope,**result}),flush=True)
    return result


def clean_native_history(manager,platform_id,st,cid=None,*,reset_legacy=False):
    """Update the pinned SDK's actual SQLite rows under one short write lock."""
    path=getattr(getattr(manager,'db',None),'db_path',None)
    if not path or not Path(path).is_file():return {'result':'unavailable','reason':'native_sqlite_not_configured'}
    permits=[{'scope':scope,'semanticBatch':True,'batchMembers':sorted(members)}
             for scope,members in _retired_members(st.db).items()]
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
