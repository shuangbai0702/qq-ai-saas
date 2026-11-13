"""Settle bookkeeping only from existing terminal evidence; never replay work."""
import json

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
