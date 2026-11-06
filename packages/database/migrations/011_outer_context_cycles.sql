-- Outer 35-effective-cycle transport checkpoints (H3/P0-B)
-- Additive only: a monotonic per-bot sequence counts effective complete
-- planning cycles and every 35th stores the checkpoint that the runtime may
-- consume once per phase to rebuild its disposable outer transport.
-- Existing rows keep their meaning and no historical data is rewritten.
ALTER TABLE autonomy_settings ADD COLUMN IF NOT EXISTS context_cycle_seq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE autonomy_settings ADD COLUMN IF NOT EXISTS context_checkpoint JSONB;
ALTER TABLE autonomy_cycles ADD COLUMN IF NOT EXISTS cycle_seq INTEGER;
CREATE INDEX IF NOT EXISTS autonomy_cycles_seq_idx
 ON autonomy_cycles(bot_id,tenant_id,cycle_seq) WHERE cycle_seq IS NOT NULL;
