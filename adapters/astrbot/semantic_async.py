"""Run local checkpoint retirement on a connection owned by its worker thread."""
import asyncio
import copy
import sqlite3
from contextlib import closing
from types import SimpleNamespace


def busy(store):
    task=getattr(store,'_semantic_worker',None)
    return task is not None and not task.done()


def track_worker(store,task):
    workers=getattr(store,'_local_db_workers',None)
    if workers is None:workers=store._local_db_workers=set()
    workers.add(task)
    def completed(_):
        workers.discard(task)
        if getattr(store,'_closing',False) and not workers:store._close_storage()
    task.add_done_callback(completed)
    return task


async def drain_store(store):
    """Revoke publication before a configuration replacement, then join I/O."""
    store.reflection.closed=True;store.reflection.binding=None
    reflection_task=store.reflection.task
    if reflection_task and not reflection_task.done():reflection_task.cancel()
    async def drain():
        while getattr(store,'_local_db_workers',None):
            await asyncio.gather(*tuple(store._local_db_workers),return_exceptions=True)
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
    value.autonomous_experience=copy.deepcopy(getattr(store,'autonomous_experience',{}))
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
    if any(j._staged is not None for j in store.model_journals.values()):return {'result':'journal_busy'}
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    if not path:raise ValueError('semantic_retirement_requires_file_database')
    snapshot=_snapshot(store)
    task=track_worker(store,asyncio.create_task(asyncio.to_thread(_finish,snapshot,path,copy.deepcopy(cfg),scope,tuple(store.model_journals))))
    store._semantic_worker=task
    cancelled=False
    try:
        try:result,scopes=await settled(task)
        except asyncio.CancelledError:
            cancelled=True
            result,scopes=task.result()
        if not getattr(store,'_closing',False) and is_current():
            # The worker's Journal objects retain its now-closed connection.
            # Reload authoritative state onto the owner's connection instead.
            for key in scopes:store.model_journals[key]=ReadonlyJournal(store.db,key)
            _prune_hot_memory(store)
            store.semantic_retirement_status=result
        if cancelled:raise asyncio.CancelledError
        return result
    finally:
        if getattr(store,'_semantic_worker',None) is task:store._semantic_worker=None


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
