"""Resolve an existing main conversation; never synthesize a parallel one."""


def scope_for(store):
    from readonly_journal import ContextRecoveryUnavailable
    from shared_context import main_journal_scope
    if not store.shared_context:raise ContextRecoveryUnavailable('shared_main_context_required')
    return main_journal_scope(store.db)


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
