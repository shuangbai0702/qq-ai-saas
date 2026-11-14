-- Original Work/receipt owners must be drained under the maintenance fence.
LOCK TABLE autonomy_cycles,tool_effects IN ACCESS EXCLUSIVE MODE;
SELECT 1 / (NOT EXISTS(SELECT 1 FROM autonomy_cycles WHERE finished_at IS NULL))::integer;
SELECT 1 / (NOT EXISTS(SELECT 1 FROM tool_effects WHERE state IN ('queued','claimed','dispatching','unknown') OR completion_state IN ('pending','claimed','unknown')))::integer;
-- The release operator transfers only confirmed object-ownership receipts to
-- the original SQLite pipeline_action_effects, verifies them there, then sets
-- this transaction-local digest. A missing proof cannot drop that authority.
SELECT 1 / (COALESCE(current_setting('fishbot.receipt_transfer_verified',true),'')=
 (SELECT md5(COALESCE(string_agg(e.id||':'||jsonb_build_object('account',e.account_uid,'aid',e.args->>'aid','status','confirmed','platformObjectId',e.result->'receipt'->>'platformObjectId')::text,'|' ORDER BY e.id),'')) FROM tool_effects e WHERE e.operation IN ('comment','dynamic_post') AND e.result->'receipt'->>'status'='confirmed')
 OR NOT EXISTS(SELECT 1 FROM tool_effects WHERE operation IN ('comment','dynamic_post') AND result->'receipt'->>'status'='confirmed'))::integer;
-- Preserve already charged external capacity in the existing cycle budget.
WITH by_work AS (
 SELECT cycle_id,work_id,jsonb_object_agg('bilibili:'||id,jsonb_build_object('status','confirmed')) AS requests
 FROM tool_effects WHERE budget_reserved AND authority='autonomous' AND cycle_id IS NOT NULL
 GROUP BY cycle_id,work_id
), merged AS (
 SELECT w.cycle_id,jsonb_object_agg(w.work_id,w.requests||COALESCE(c.budget->'qqExternalRequests'->w.work_id,'{}'::jsonb)) AS requests
 FROM by_work w JOIN autonomy_cycles c ON c.id=w.cycle_id GROUP BY w.cycle_id
)
UPDATE autonomy_cycles c SET budget=jsonb_set(c.budget,'{qqExternalRequests}',COALESCE(c.budget->'qqExternalRequests','{}'::jsonb)||m.requests)
FROM merged m WHERE m.cycle_id=c.id;
-- Finished historical totals already belong to autonomy_cycles/daily_usage.
-- Raw args, result narratives and cancellation history are intentionally retired.
DROP TABLE tool_effects;
DROP TABLE tool_cancellations;
