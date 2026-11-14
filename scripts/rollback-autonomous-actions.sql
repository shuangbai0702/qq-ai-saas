-- Explicit downgrade only, after the normal execution/IPC maintenance fence.
-- No current runtime imports this file. Recreate minimal original Work/quota
-- identities for the previous program; retired execution narratives stay retired.
LOCK TABLE autonomy_cycles,tool_effects IN ACCESS EXCLUSIVE MODE;
SELECT 1 / (NOT EXISTS(SELECT 1 FROM autonomy_cycles WHERE finished_at IS NULL))::integer;
CREATE TABLE autonomy_actions(
 id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,
 epoch INTEGER NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,objective TEXT NOT NULL,
 goal_id TEXT,status TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL,
 receipt JSONB,result JSONB,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),finished_at TIMESTAMPTZ,
 FOREIGN KEY(cycle_id,bot_id,tenant_id) REFERENCES autonomy_cycles(id,bot_id,tenant_id),
 FOREIGN KEY(bot_id,goal_id) REFERENCES autonomy_goals(bot_id,id)
);
CREATE INDEX autonomy_actions_pending ON autonomy_actions(bot_id,status);
WITH owners AS (
 SELECT c.id AS work_id,c.id AS cycle_id FROM autonomy_cycles c WHERE c.work_expires_at IS NOT NULL
 UNION SELECT k.key,c.id FROM autonomy_cycles c CROSS JOIN LATERAL jsonb_object_keys(COALESCE(c.budget->'qqExternalRequests','{}'::jsonb)) k(key)
 UNION SELECT e.work_id,e.cycle_id FROM tool_effects e WHERE e.cycle_id IS NOT NULL
), facts AS (
 SELECT o.work_id,c.*,
  c.decision->>'receiptPending'='true' OR c.reason='lease_expired' OR EXISTS(
   SELECT 1 FROM jsonb_each(COALESCE(c.budget->'qqExternalRequests'->o.work_id,'{}'::jsonb)) q
   WHERE q.value->>'status' IN ('reserved','unknown')
  ) AS uncertain
 FROM owners o JOIN autonomy_cycles c ON c.id=o.cycle_id
)
INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at)
SELECT work_id,id,bot_id,tenant_id,epoch,'research','','回退后仅核对原Work回执',
 CASE WHEN uncertain THEN 'unknown' ELSE 'completed' END,
 COALESCE(work_expires_at,finished_at,started_at),
 jsonb_build_object('status',CASE WHEN uncertain THEN 'unknown' ELSE 'completed' END,'effects','[]'::jsonb),
 started_at,finished_at FROM facts;
ALTER TABLE tool_effects DROP COLUMN cycle_id;
ALTER TABLE autonomy_cycles DROP COLUMN work_expires_at;
DELETE FROM schema_migrations WHERE version='014';
