-- Retire unused migration-011 outer cycle columns and legacy autonomy_settings fields.
-- Historical migrations 001..020 remain immutable.
DROP INDEX IF EXISTS autonomy_cycles_seq_idx;
ALTER TABLE autonomy_cycles DROP COLUMN IF EXISTS cycle_seq;
ALTER TABLE autonomy_settings DROP COLUMN IF EXISTS context_cycle_seq;
ALTER TABLE autonomy_settings DROP COLUMN IF EXISTS context_checkpoint;
ALTER TABLE autonomy_settings DROP COLUMN IF EXISTS summary;
ALTER TABLE autonomy_settings DROP COLUMN IF EXISTS wait;
