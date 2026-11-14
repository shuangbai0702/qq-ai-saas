-- Transfer quota reservations, not execution narratives or protocol receipts.
-- Run under the normal migration transaction and release maintenance fence.
-- Abort rather than overwrite any reservation already owned by the new budget.
SELECT 1 / (NOT EXISTS(SELECT 1 FROM autonomy_cycles
 WHERE COALESCE(budget->'qqExternalRequests','{}'::jsonb)<>'{}'::jsonb))::integer;

WITH reservations AS (
 SELECT cycle_id,jsonb_object_agg(id::text,external_effects) AS requests
 FROM autonomy_actions WHERE external_effects<>'{}'::jsonb GROUP BY cycle_id
)
UPDATE autonomy_cycles c
SET budget=jsonb_set(c.budget,'{qqExternalRequests}',r.requests)
FROM reservations r WHERE c.id=r.cycle_id;

ALTER TABLE autonomy_actions DROP COLUMN external_effects;
