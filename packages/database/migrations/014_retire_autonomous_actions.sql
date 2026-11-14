-- Apply only after all execution owners reach the existing maintenance fence.
-- Unknown dispatches cannot be discarded as historical task data.
SELECT 1 / (NOT EXISTS(SELECT 1 FROM autonomy_actions WHERE status IN ('planned','executing','unknown') OR status='partial' AND result->'effects' @> '[{"status":"unknown"}]'::jsonb))::integer;
SELECT 1 / (NOT EXISTS(SELECT 1 FROM autonomy_cycles WHERE finished_at IS NULL))::integer;
SELECT 1 / (NOT EXISTS(SELECT 1 FROM tool_effects WHERE state IN ('queued','claimed','dispatching','unknown') OR completion_state IN ('pending','claimed','unknown')))::integer;
ALTER TABLE autonomy_cycles ADD COLUMN work_expires_at TIMESTAMPTZ;
ALTER TABLE tool_effects ADD COLUMN cycle_id TEXT;
UPDATE tool_effects e SET cycle_id=a.cycle_id,
 origin_scope=CASE WHEN e.origin_scope<>'' THEN e.origin_scope ELSE COALESCE(g.source->>'scope','self') END,
 origin_event=CASE WHEN e.origin_event<>'' THEN e.origin_event ELSE COALESCE(g.source->>'eventId','') END
 FROM autonomy_actions a LEFT JOIN autonomy_goals g ON g.id=a.goal_id AND g.bot_id=a.bot_id AND g.tenant_id=a.tenant_id
 WHERE e.authority='autonomous' AND a.id=e.work_id AND a.bot_id=e.bot_id AND a.tenant_id=e.tenant_id;
ALTER TABLE tool_effects ADD CONSTRAINT tool_effect_cycle FOREIGN KEY(cycle_id,bot_id,tenant_id) REFERENCES autonomy_cycles(id,bot_id,tenant_id);
CREATE INDEX tool_effect_cycle_budget ON tool_effects(cycle_id) WHERE cycle_id IS NOT NULL;
-- Retire old planner payloads. They are operational metadata, never Journal.
UPDATE autonomy_cycles SET decision=decision-'action'-'expectedGoalRevision'-'goalId';
DROP TABLE autonomy_actions;
