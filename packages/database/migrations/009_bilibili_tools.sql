-- Legacy PostgreSQL cutovers may lack this composite tenant ownership key.
CREATE UNIQUE INDEX IF NOT EXISTS bots_bilibili_tenant_identity ON bots(id,tenant_id);

CREATE TABLE bilibili_settings(
 bot_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,config JSONB NOT NULL,
 model_secret TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);
CREATE TABLE bilibili_accounts(
 bot_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,uid TEXT NOT NULL,label TEXT NOT NULL,secret TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1,state TEXT NOT NULL DEFAULT 'ready',refresh_state TEXT NOT NULL DEFAULT 'idle',pending_confirm TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id),UNIQUE(uid)
);
CREATE TABLE bilibili_logins(
 bot_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,id TEXT NOT NULL,secret TEXT NOT NULL,state TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);
CREATE TABLE tool_effects(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,bot_id TEXT NOT NULL,work_id TEXT NOT NULL,step_id TEXT NOT NULL,
 operation TEXT NOT NULL,args JSONB NOT NULL,args_digest TEXT NOT NULL,authority TEXT NOT NULL,sender_id TEXT,mode_epoch INTEGER,
 generation INTEGER NOT NULL,config_revision INTEGER NOT NULL,settings_revision INTEGER NOT NULL,account_revision INTEGER,account_uid TEXT,
 state TEXT NOT NULL DEFAULT 'queued',lease_owner TEXT,lease_until TIMESTAMPTZ,cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 deadline_at TIMESTAMPTZ NOT NULL,result JSONB,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id),UNIQUE(bot_id,work_id,step_id)
);
CREATE INDEX tool_effects_queue ON tool_effects(state,created_at);
CREATE INDEX tool_effects_bot ON tool_effects(bot_id,created_at);
CREATE TABLE tool_service_cooldowns(service TEXT PRIMARY KEY,until_at TIMESTAMPTZ NOT NULL,reason TEXT NOT NULL);

-- Cancellation can arrive before admission's response (or before admission).
CREATE TABLE tool_cancellations(
 bot_id TEXT NOT NULL,tenant_id TEXT NOT NULL,work_id TEXT NOT NULL,step_id TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '10 minutes',
 PRIMARY KEY(bot_id,work_id,step_id),
 FOREIGN KEY(bot_id,tenant_id) REFERENCES bots(id,tenant_id)
);

-- Preserve an existing explicitly enabled public-search capability when moving
-- its controls to the independent Bilibili section. No private/write grants.
INSERT INTO bilibili_settings(bot_id,tenant_id,config)
SELECT b.id,b.tenant_id,jsonb_build_object('enabled',true,'scopes',jsonb_build_array('public.read'),'allowAutonomous',true)
FROM bots b JOIN bot_config_revisions c ON c.bot_id=b.id AND c.revision=b.revision
WHERE b.deleted_at IS NULL AND c.config->'behavior'->>'bilibiliSearchEnabled'='true'
ON CONFLICT(bot_id) DO NOTHING;
