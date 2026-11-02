ALTER TABLE bots ADD CONSTRAINT bots_id_tenant_unique UNIQUE(id,tenant_id);
CREATE TABLE autonomy_settings(
 bot_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,epoch INTEGER NOT NULL DEFAULT 1,runtime_epoch INTEGER NOT NULL DEFAULT 0,
 config JSONB NOT NULL,state TEXT NOT NULL DEFAULT 'disabled',goal_revision INTEGER NOT NULL DEFAULT 0,
 summary TEXT NOT NULL DEFAULT '',wait JSONB NOT NULL DEFAULT '{}',observation JSONB NOT NULL DEFAULT '{}',last_error TEXT,last_event_at TIMESTAMPTZ,
 lease_owner TEXT,lease_until TIMESTAMPTZ,next_wake_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);
CREATE TABLE autonomy_goals(
 id TEXT NOT NULL,bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,origin TEXT NOT NULL,
 status TEXT NOT NULL,title TEXT NOT NULL,reason TEXT NOT NULL,progress TEXT NOT NULL DEFAULT '',
 success_condition TEXT NOT NULL DEFAULT '',source JSONB NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(bot_id,id),FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);
CREATE TABLE autonomy_cycles(
 id TEXT PRIMARY KEY,bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,epoch INTEGER NOT NULL,
 generation INTEGER NOT NULL,config_revision INTEGER NOT NULL,owner TEXT NOT NULL,
 state TEXT NOT NULL,budget JSONB NOT NULL,usage JSONB NOT NULL DEFAULT '{}',
 reason TEXT,decision JSONB,started_at TIMESTAMPTZ NOT NULL DEFAULT now(),finished_at TIMESTAMPTZ,
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id),UNIQUE(id,bot_id,tenant_id)
);
CREATE TABLE autonomy_actions(
 id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,
 epoch INTEGER NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,objective TEXT NOT NULL,
 goal_id TEXT,status TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL,
 receipt JSONB,result JSONB,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),finished_at TIMESTAMPTZ,
 FOREIGN KEY(cycle_id,bot_id,tenant_id) REFERENCES autonomy_cycles(id,bot_id,tenant_id),
 FOREIGN KEY(bot_id,goal_id) REFERENCES autonomy_goals(bot_id,id)
);
CREATE TABLE autonomy_wakes(
 bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,dedupe_key TEXT NOT NULL,
 epoch INTEGER NOT NULL,due_at TIMESTAMPTZ NOT NULL,reason TEXT NOT NULL,
 scope TEXT NOT NULL DEFAULT '',created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(bot_id,dedupe_key),FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);
CREATE INDEX autonomy_wakes_due ON autonomy_wakes(due_at,bot_id);
CREATE INDEX autonomy_actions_pending ON autonomy_actions(bot_id,status);
CREATE INDEX autonomy_cycles_bot ON autonomy_cycles(bot_id,started_at DESC);
CREATE TABLE autonomy_daily_usage(
 bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,day TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
 used_tokens INTEGER NOT NULL DEFAULT 0 CHECK(used_tokens>=0),
 model_calls INTEGER NOT NULL DEFAULT 0,tool_calls INTEGER NOT NULL DEFAULT 0,
 external_actions INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(bot_id,day),FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);
