-- One-shot operator downgrade 015 -> 014, never loaded by current runtime.
-- Hold the original Agent/API/IPC maintenance fence. Verify EVERY original
-- SQLite Work has no active/unknown tool execution or unconsumed completion,
-- reconcile original budgets, export only confirmed comment/dynamic ownership
-- receipts into the TEMP table below, then set both transaction-local proofs.
-- No raw arguments, analysis, transcript or historical task payload is restored.
SELECT 1 / (COALESCE(current_setting('fishbot.tool_work_drained',true),'')='true')::integer;
LOCK TABLE autonomy_cycles IN ACCESS EXCLUSIVE MODE;
SELECT 1 / (NOT EXISTS(SELECT 1 FROM autonomy_cycles WHERE finished_at IS NULL))::integer;
CREATE TEMP TABLE IF NOT EXISTS fishbot_tool_rollback_receipts(
 id TEXT PRIMARY KEY,bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,
 operation TEXT NOT NULL,args_digest TEXT NOT NULL,account_uid TEXT,aid TEXT,
 platform_object_id TEXT NOT NULL,at TIMESTAMPTZ NOT NULL
);
SELECT 1 / (COALESCE(current_setting('fishbot.rollback_receipts_verified',true),'')=
 (SELECT md5(COALESCE(string_agg(id||':'||platform_object_id,'|' ORDER BY id),'')) FROM fishbot_tool_rollback_receipts))::integer;
SELECT 1 / (NOT EXISTS(SELECT 1 FROM fishbot_tool_rollback_receipts WHERE operation NOT IN ('comment','dynamic_post')))::integer;
CREATE TABLE tool_effects(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,bot_id TEXT NOT NULL,work_id TEXT NOT NULL,step_id TEXT NOT NULL,
 operation TEXT NOT NULL,args JSONB NOT NULL,args_digest TEXT NOT NULL,authority TEXT NOT NULL,sender_id TEXT,mode_epoch INTEGER,
 generation INTEGER NOT NULL,config_revision INTEGER NOT NULL,settings_revision INTEGER NOT NULL,account_revision INTEGER,account_uid TEXT,
 state TEXT NOT NULL DEFAULT 'queued',lease_owner TEXT,lease_until TIMESTAMPTZ,cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 deadline_at TIMESTAMPTZ NOT NULL,result JSONB,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 origin_scope TEXT NOT NULL DEFAULT '',origin_event TEXT NOT NULL DEFAULT '',continuation JSONB NOT NULL DEFAULT '{}',
 completion_state TEXT NOT NULL DEFAULT 'none',completion_owner TEXT,completion_until TIMESTAMPTZ,usage_checkpoint JSONB NOT NULL DEFAULT '{}',
 budget_reserved BOOLEAN NOT NULL DEFAULT false,cycle_id TEXT,
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id),UNIQUE(bot_id,work_id,step_id),
 CONSTRAINT tool_effect_cycle FOREIGN KEY(cycle_id,bot_id,tenant_id) REFERENCES autonomy_cycles(id,bot_id,tenant_id)
);
CREATE INDEX tool_effects_queue ON tool_effects(state,created_at);
CREATE INDEX tool_effects_bot ON tool_effects(bot_id,created_at);
CREATE INDEX tool_effects_completion ON tool_effects(bot_id,completion_state);
CREATE INDEX tool_effect_cycle_budget ON tool_effects(cycle_id) WHERE cycle_id IS NOT NULL;
CREATE TABLE tool_cancellations(
 bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,work_id TEXT NOT NULL,step_id TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '10 minutes',PRIMARY KEY(bot_id,work_id,step_id),
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);
INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,step_id,operation,args,args_digest,authority,generation,config_revision,settings_revision,account_uid,state,deadline_at,result,created_at,updated_at)
 SELECT r.id,r.tenant_id,r.bot_id,r.id,'ownership-receipt',r.operation,
 CASE WHEN r.aid IS NULL THEN '{}'::jsonb ELSE jsonb_build_object('aid',r.aid) END,
 r.args_digest,'foreground',b.generation,b.revision,COALESCE(s.revision,1),r.account_uid,'finished',r.at,
 jsonb_build_object('receipt',jsonb_build_object('status','confirmed','platformObjectId',r.platform_object_id)),r.at,r.at
 FROM fishbot_tool_rollback_receipts r JOIN bots b ON b.id=r.bot_id AND b.tenant_id=r.tenant_id
 LEFT JOIN bilibili_settings s ON s.bot_id=r.bot_id AND s.tenant_id=r.tenant_id;
SELECT 1 / ((SELECT count(*) FROM tool_effects)=(SELECT count(*) FROM fishbot_tool_rollback_receipts))::integer;
DELETE FROM schema_migrations WHERE version='015';
