"""Authoritative forget / reflection-epoch invalidation across derived memory."""
from core_note import forget_core_notes
from memory_policy import person_filter,shared_person


def invalidate(db,scope,subject=''):
    forget_core_notes(db,scope,subject)
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Inference prose may mention a forgotten person without a structured link.
    # Clear this bot's bounded decision rationales conservatively on forgetting.
    if 'autonomy_decisions' in tables and 'rationale' in {r[1] for r in db.execute('PRAGMA table_info(autonomy_decisions)')}:db.execute('UPDATE autonomy_decisions SET rationale=NULL')
    if 'memory_summaries' not in tables:return
    if 'memory_summary_origins' in tables:
        db.execute('DELETE FROM memory_summaries WHERE id IN (SELECT summary_id FROM memory_summary_origins WHERE scope=?)',(scope,))
    # Corrections/deletions invalidate derived material, not just primary facts.
    if shared_person(scope,subject) and 'memory_summary_people' in tables:
        where,args=person_filter(scope,subject)
        # Include legacy source scopes as well as summaries with exact event
        # attribution. A correction in private cannot leave an old group copy.
        db.execute('DELETE FROM memory_summaries WHERE scope=? OR id IN (SELECT summary_id FROM memory_summary_people WHERE subject=?) OR scope IN (SELECT scope FROM facts WHERE '+where+')',(scope,subject)+args)
        where,args=person_filter(scope,subject)
    else:
        db.execute('DELETE FROM memory_summaries WHERE scope=?',(scope,))
    where,args=person_filter(scope,subject) if subject else ('scope=?',(scope,))
    db.execute('DELETE FROM memory_rounds WHERE '+where,args)
    if 'memory_pending' in tables:
        db.execute('DELETE FROM memory_pending WHERE '+where,args)
    db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1')
    if 'memory_reconstruction_checkpoints' in tables:
        if 'conversation_continuity' in {r[1] for r in db.execute('PRAGMA table_info(memory_reconstruction_checkpoints)')}:
            # This derived prose may mention a forgotten person without a
            # structured subject link. Match the existing bot-wide epoch fence.
            db.execute('UPDATE memory_reconstruction_checkpoints SET conversation_continuity=NULL')
        db.execute("UPDATE memory_reconstruction_checkpoints SET state='invalidated',request_id=NULL,pending_events=NULL WHERE state IN ('available','reserved','pending_coverage','pending_merge')")
