ALTER TABLE qq_bindings ADD COLUMN binding_kind TEXT NOT NULL DEFAULT 'real' CHECK (binding_kind IN ('real','mock'));
UPDATE qq_bindings SET binding_kind='mock' WHERE bot_id IN (SELECT id FROM bots WHERE protocol='mock');
ALTER TABLE qq_bindings DROP CONSTRAINT qq_bindings_qq_uin_key;
CREATE UNIQUE INDEX qq_bindings_kind_uin_unique ON qq_bindings(binding_kind,qq_uin);
