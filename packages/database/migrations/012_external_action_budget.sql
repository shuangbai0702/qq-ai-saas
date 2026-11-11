ALTER TABLE autonomy_actions ADD COLUMN external_effects JSONB NOT NULL DEFAULT '{}';
ALTER TABLE tool_effects ADD COLUMN budget_reserved BOOLEAN NOT NULL DEFAULT false;
