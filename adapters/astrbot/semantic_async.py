"""Run local checkpoint retirement on a connection owned by its worker thread."""
import asyncio
import copy
import sqlite3
from contextlib import closing
from types import SimpleNamespace


def busy(store):
    task=getattr(store,'semantic_worker',None)
    return task is not None and not task.done()


def track_worker(store,task):
    workers=getattr(store,'local_db_workers',None)
    if workers is None:workers=store.local_db_workers=set()
    workers.add(task)
    def completed(_):
        workers.discard(task)
        if getattr(store,'closing',False) and not workers:store.close_storage()
    task.add_done_callback(completed)
    return task


async def drain_store(store):
    """Revoke publication before a configuration replacement, then join I/O."""
    store.reflection.closed=True;store.reflection.binding=None
    reflection_task=store.reflection.task
    if reflection_task and not reflection_task.done():reflection_task.cancel()
    async def drain():
        while getattr(store,'local_db_workers',None):
            await asyncio.gather(*tuple(store.local_db_workers),return_exceptions=True)
        if reflection_task and reflection_task is not asyncio.current_task():
            await asyncio.gather(reflection_task,return_exceptions=True)
    await settled(asyncio.create_task(drain()))


async def settled(task):
    # Cancellation cannot release the compression writer while its transaction
    # is still running. Drain it first; the original caller still sees cancel.
    cancelled=False
    while not task.done():
        try:await asyncio.shield(task)
        except asyncio.CancelledError:cancelled=True
    result=task.result()
    if cancelled:raise asyncio.CancelledError
    return result


def _snapshot(store):
    value=SimpleNamespace(root=store.root,shared_context=store.shared_context,
                          semantic_native=getattr(store,'semantic_native',None))
    value.model_journals={}
    value.ambient=copy.deepcopy(store.ambient)
    value.participation=copy.copy(store.participation)
    value.participation.scopes=copy.deepcopy(store.participation.scopes)
    value.reflection=copy.copy(store.reflection)
    value.memory=copy.copy(store.memory)
    value.pipeline=copy.copy(store.pipeline)
    value.db=None
    for helper in (value.reflection,value.memory,value.participation,value.pipeline):helper.db=None
    value.reflection.memory=value.memory
    for name in ('task','binding','admission','recapture','checkpoint_callback'):
        setattr(value.reflection,name,None)
    return value


def _finish(snapshot,path,cfg,scope,journal_scopes):
    from readonly_journal import ReadonlyJournal
    from semantic_runtime import finish_semantic_batches
    # Never pass the event-loop SQLite handle across threads, even when the
    # underlying SQLite build is serialized.
    with closing(sqlite3.connect(path,timeout=30)) as db:
        snapshot.db=db
        for helper in (snapshot.reflection,snapshot.memory,snapshot.participation,snapshot.pipeline):helper.db=db
        snapshot.reflection.memory=snapshot.memory
        snapshot.model_journals={key:ReadonlyJournal(db,key) for key in journal_scopes}
        result=finish_semantic_batches(snapshot,cfg,scope)
        scopes=tuple(snapshot.model_journals)
        return result,scopes


async def finish(store,cfg,scope,is_current=lambda:True):
    from readonly_journal import ReadonlyJournal
    from semantic_runtime import _prune_hot_memory
    if busy(store):raise RuntimeError('semantic_retirement_already_running')
    if store.db.in_transaction:return {'result':'transaction_busy'}
    if any(j.has_staged for j in store.model_journals.values()):return {'result':'journal_busy'}
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    if not path:raise ValueError('semantic_retirement_requires_file_database')
    snapshot=_snapshot(store)
    task=track_worker(store,asyncio.create_task(asyncio.to_thread(_finish,snapshot,path,copy.deepcopy(cfg),scope,tuple(store.model_journals))))
    store.semantic_worker=task
    cancelled=False
    try:
        try:result,scopes=await settled(task)
        except asyncio.CancelledError:
            cancelled=True
            result,scopes=task.result()
        if not getattr(store,'closing',False) and is_current():
            # The worker's Journal objects retain its now-closed connection.
            # Reload authoritative state onto the owner's connection instead.
            for key in scopes:store.model_journals[key]=ReadonlyJournal(store.db,key)
            _prune_hot_memory(store)
            store.semantic_retirement_status=result
        if cancelled:raise asyncio.CancelledError
        return result
    finally:
        if getattr(store,'semantic_worker',None) is task:store.semantic_worker=None


def persist_input(path,pipeline,event,lease):
    """Same durable input ledger; contention waits off the shared event loop."""
    with closing(sqlite3.connect(path,timeout=30)) as db:
        local=copy.copy(pipeline);local.db=db
        return local.persist_input(event,lease)


async def clean_native(store,manager,platform,cid=None):
    from semantic_runtime import clean_native_history
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    if not path:raise ValueError('semantic_retirement_requires_file_database')
    # Native cleanup only reads this bot's authoritative ledger. The SDK DB
    # connection used by clean_native_history is also created in this thread.
    snapshot=SimpleNamespace(shared_context=store.shared_context,model_journals={})
    def run():
        with closing(sqlite3.connect(path,timeout=30)) as db:
            snapshot.db=db
            return clean_native_history(manager,platform,snapshot,cid)
    return await settled(track_worker(store,asyncio.create_task(asyncio.to_thread(run))))


def install_native_retirement_guard(manager,stores,settings):
    """Fence actual SDK commits and reads against the existing retired batches.

    The SDK and Journal use different databases: the post-await read fence is
    mandatory even when physical deletion fails. No second retirement ledger.
    """
    from semantic_runtime import _native_source,_retired_members,_retired_row
    db=getattr(manager,'db',None)
    if db is None or getattr(db,'_semantic_retirement_guard',False):return
    def owner(platform):
        st=stores.get(platform)
        return st if manager.db is db and st is not None and platform in settings and not getattr(st.reflection,'closed',False) else None
    async def cleanup(platform,st,cid=None):
        lease=settings.get(platform)
        if owner(platform) is not st or settings.get(platform) is not lease:return {'result':'pending','reason':'stale_native_owner'}
        try:status=await clean_native(st,manager,platform,cid)
        except (OSError,sqlite3.Error,ValueError,TypeError) as error:status={'result':'pending','reason':type(error).__name__}
        if owner(platform) is not st or settings.get(platform) is not lease:return {'result':'pending','reason':'stale_native_owner'}
        st.semantic_native_status=status
        return status
    if hasattr(db,'update_conversation'):
        original=db.update_conversation
        async def update(*args,**kwargs):
            value=await original(*args,**kwargs)
            platform=getattr(value,'platform_id',None);st=owner(platform)
            if st is not None:await cleanup(platform,st,getattr(value,'conversation_id',None))
            return value
        db.update_conversation=update
    if hasattr(db,'insert_platform_message_history'):
        original_insert=db.insert_platform_message_history
        async def insert(*args,**kwargs):
            platform=kwargs.get('platform_id',args[0] if args else None);st=owner(platform)
            content=kwargs.get('content',args[2] if len(args)>2 else None)
            if st is not None and isinstance(content,dict):
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
            st=owner(platform)
            if st is not None:
                status=await cleanup(platform,st)
                if owner(platform) is not st:return None
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
