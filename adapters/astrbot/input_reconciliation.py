"""Settle bookkeeping only from existing terminal evidence; never replay work."""
import json
import time


def ambient_retirement_candidates(db,tables,*,recovery=False):
    """Group observations with no live inbox or outstanding response owner."""
    required={'pipeline_work_inputs','pipeline_scope_work','pipeline_events','pipeline_action_effects'}
    if not required<=tables:return set()
    from bot_pipeline import RECOVERABLE_ADMISSION_REASONS
    reasons=','.join('?' for _ in RECOVERABLE_ADMISSION_REASONS)
    status=("i.status='blocked_recovery' AND i.payload IS NULL AND i.retry_count=0"
            if recovery else "i.status='deferred'")
    inbox_guard='AND NOT EXISTS(SELECT 1 FROM autonomy_group_inbox a WHERE a.scope=i.scope AND a.event=i.event)' if 'autonomy_group_inbox' in tables else ''
    model_guard="""AND NOT EXISTS(SELECT 1 FROM memory_message_sources m WHERE m.direction='model'
      AND (m.scope=i.scope AND m.event=i.event OR m.scope='bot_shared' AND m.event=json_array(i.scope,i.event))
      AND CASE WHEN json_valid(m.data) THEN coalesce(json_array_length(m.data,'$.outputs'),0)>0
        OR coalesce(json_extract(m.data,'$.settlement.attempted'),0)>0
        OR coalesce(json_extract(m.data,'$.settlement.confirmed'),0)>0 ELSE 1 END)""" if 'memory_message_sources' in tables else ''
    return set(db.execute(f"""SELECT i.scope,i.event FROM pipeline_work_inputs i
      WHERE {status} AND i.scope LIKE 'group:%' AND i.addressed_self=0 AND i.seen=0 AND i.identity IS NOT NULL
      AND NOT EXISTS(SELECT 1 FROM pipeline_scope_work w WHERE w.scope=i.scope)
      AND NOT EXISTS(SELECT 1 FROM pipeline_action_effects f WHERE f.action=i.event)
      {inbox_guard}
      {model_guard}
      AND NOT EXISTS(SELECT 1 FROM pipeline_events e WHERE e.scope=i.scope AND e.event=i.event
        AND NOT (e.subject IS i.subject AND e.stage IS 'admission' AND e.attempted IS 0 AND e.confirmed IS 0
          AND coalesce((e.status IN ('waiting','silent') AND e.reason IN ({reasons})
            OR e.status='blocked' AND e.reason='waiting_input_blocked' AND e.exit_reason='admission_blocked'),0)))
      """,RECOVERABLE_ADMISSION_REASONS))


def unresolved_retirement_inputs(db,tables,ambient):
    if 'pipeline_work_inputs' not in tables:return set()
    return set(db.execute("""SELECT scope,event FROM pipeline_work_inputs
      WHERE status IN ('pending','queued','deferred','waiting_compression') AND payload IS NOT NULL"""))-ambient


def recover_retired_observations(db):
    """Close old payloadless entries using exact consumed and purged batches."""
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'memory_semantic_batches','memory_semantic_purges','memory_reconstruction_checkpoints'}<=tables:return
    from context_protocol import model_json,scoped_event_hash
    with db:
        db.execute('BEGIN IMMEDIATE')
        candidates=ambient_retirement_candidates(db,tables,recovery=True)
        if not candidates:return
        retired=set()
        for scope,raw in db.execute("""SELECT b.scope,b.members FROM memory_semantic_batches b
          JOIN memory_reconstruction_checkpoints c ON c.id=b.checkpoint_id AND c.scope=b.scope
          JOIN memory_semantic_purges p ON p.checkpoint_id=b.checkpoint_id
          WHERE b.state='retired' AND c.state='consumed'"""):
            retired.update((scope,key) for key in json.loads(raw))
        matched=[(scope,event) for scope,event in candidates if
            (scope,scoped_event_hash(scope,event)) in retired or
            ('bot_shared',scoped_event_hash('bot_shared',model_json([scope,event]))) in retired]
        db.executemany("UPDATE pipeline_work_inputs SET status='settled_retired_observation',owner=NULL WHERE scope=? AND event=?",matched)
        if 'memory_checkpoint_issues' in tables:
            for scope in {scope for scope,_ in matched}:
                counts=dict(db.execute("SELECT status,count(*) FROM pipeline_work_inputs WHERE scope=? AND status LIKE 'blocked_%' GROUP BY status",(scope,)))
                detail=json.dumps({'scope':scope,'blocked':counts,'external_replay':False},sort_keys=True,separators=(',',':'))
                db.execute("UPDATE memory_checkpoint_issues SET status=?,detail=?,updated=? WHERE kind=?",
                           ('open' if counts else 'resolved',detail,time.time(),'input_recovery:'+scope))

def reconcile(db,reflection):
    # CAS and the active-scope fence keep the current Work's ownership intact.
    with db:
        db.execute("""UPDATE pipeline_work_inputs AS i SET status='settled_proven',owner=NULL
          WHERE i.status LIKE 'blocked_%'
          AND NOT EXISTS(SELECT 1 FROM pipeline_scope_work w WHERE w.scope=i.scope)
          AND EXISTS(SELECT 1 FROM pipeline_events e WHERE e.scope=i.scope AND e.event=i.event
            AND e.subject=i.subject AND e.status IN ('sent','silent','superseded','combined')
            AND e.attempted=e.confirmed AND e.finished_at IS NOT NULL)""")
        # The input authority retains exact keys and original error evidence.
        # Resolve only redundant per-event issue rows backed by that authority.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_checkpoint_issues'").fetchone():return
        rows=db.execute("SELECT scope,checkpoint_id,kind,detail FROM memory_checkpoint_issues WHERE status='open' AND kind LIKE 'input_recovery:%' LIMIT 128").fetchall()
        for scope,checkpoint,kind,raw in rows:
            try:detail=json.loads(raw)
            except (TypeError,ValueError):continue
            if not isinstance(detail,dict) or not detail.get('scope') or not detail.get('event'):continue
            if kind!='input_recovery:'+detail['scope']+':'+str(detail['event']):continue
            row=db.execute('SELECT status FROM pipeline_work_inputs WHERE scope=? AND event=?',(detail['scope'],str(detail['event']))).fetchone()
            if row is None:continue # Missing evidence is still visible, never success.
            reflection.checkpoint_issue(scope,checkpoint,'input_recovery:'+detail['scope'],{'scope':detail['scope']})
            db.execute("UPDATE memory_checkpoint_issues SET status='resolved' WHERE scope=? AND checkpoint_id=? AND kind=? AND status='open'",(scope,checkpoint,kind))
