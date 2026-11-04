ALTER TABLE tool_effects ADD COLUMN origin_scope TEXT NOT NULL DEFAULT '';
ALTER TABLE tool_effects ADD COLUMN origin_event TEXT NOT NULL DEFAULT '';
ALTER TABLE tool_effects ADD COLUMN continuation JSONB NOT NULL DEFAULT '{}';
ALTER TABLE tool_effects ADD COLUMN completion_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE tool_effects ADD COLUMN completion_owner TEXT;
ALTER TABLE tool_effects ADD COLUMN completion_until TIMESTAMPTZ;
CREATE INDEX tool_effects_completion ON tool_effects(bot_id,completion_state);
ALTER TABLE tool_effects ADD COLUMN usage_checkpoint JSONB NOT NULL DEFAULT '{}';
