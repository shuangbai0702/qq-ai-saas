"""Resolve an existing main conversation; never synthesize a parallel one."""


def canonical_main(store,event):
    """A service block in this submitted scope makes the Journal authoritative.

    Native SDK conversation windows do not contain internally activated turns.
    Read the existing prefix itself, including after a process restart; no new
    routing flag or parallel history is persisted.
    """
    if event.get_extra('_hosted_main_activation'):return True
    if event.get_extra('_hosted_internal_work'):return False
    from bot_pipeline import delivery_scope
    from readonly_journal import ReadonlyJournal
    from context_protocol import source
    scope=delivery_scope(event)
    journal=store.model_journals.get(scope)
    if journal is None:
        journal=ReadonlyJournal(store.db,scope)
        if journal.last:store.model_journals[scope]=journal
    return bool(journal.last and (journal.last.main_conversation or any((o:=source(m)) and o.get('kind')=='platform_service_not_user'
                and o['scope']==scope for m in journal.last.base)))


def scope_for(store, config):
    if not store.shared_context:raise ValueError('shared_main_context_required')
    from shared_context import JOURNAL_SCOPE
    return JOURNAL_SCOPE


def event_key(work, logical):
    from context_protocol import model_json
    return model_json([work.scope, str(work.event_id)]) if logical == 'bot_shared' else str(work.event_id)


def tail_service(prepared, journal, work):
    """Only the NEW activation block moves behind this request's new packets."""
    from context_protocol import source
    prefix = len(journal.last.wire) if journal.last else 0
    indices = [i for i,m in enumerate(prepared.wire) if (o:=source(m)) and
               o.get('kind')=='platform_service_not_user' and o['scope']==work.scope and str(o['event_id'])==str(work.event_id)]
    if not indices:return
    index=indices[-1]
    if index < prefix and prepared.wire[index] in journal.last.wire:return  # Never move a submitted block on tool continuation.
    # An activation's initial input has no following tool pair. Avoid moving
    # its anchor across a new assistant/tool continuation if an SDK differs.
    if any(m.get('role') in ('assistant','tool') for m in prepared.wire[index+1:]):return
    prepared.wire.append(prepared.wire.pop(index))
