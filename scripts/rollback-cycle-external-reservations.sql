-- Operator rollback only: hold the existing maintenance fence and stop all
-- database writers. Execute in one transaction before loading pre-013 code.
ALTER TABLE autonomy_actions ADD COLUMN external_effects JSONB NOT NULL DEFAULT '{}';

-- Abort the whole rollback if even one reservation has no original owner.
SELECT 1 / (NOT EXISTS(
  SELECT 1 FROM autonomy_cycles c
  CROSS JOIN LATERAL jsonb_each(COALESCE(c.budget->'qqExternalRequests','{}'::jsonb)) w
  WHERE NOT EXISTS(SELECT 1 FROM autonomy_actions a WHERE a.id=w.key
   AND a.cycle_id=c.id AND a.bot_id=c.bot_id AND a.tenant_id=c.tenant_id)
 ))::integer;

UPDATE autonomy_actions a SET external_effects=c.budget->'qqExternalRequests'->a.id
FROM autonomy_cycles c WHERE a.cycle_id=c.id AND a.bot_id=c.bot_id
 AND a.tenant_id=c.tenant_id AND c.budget->'qqExternalRequests' ? a.id;

UPDATE autonomy_cycles SET budget=budget-'qqExternalRequests'
WHERE budget ? 'qqExternalRequests';
DELETE FROM schema_migrations WHERE version='013';
