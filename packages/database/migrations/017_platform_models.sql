CREATE TABLE platform_model_providers (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 base_url TEXT NOT NULL,
 model TEXT NOT NULL,
 secret TEXT NOT NULL,
 priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 10000),
 enabled BOOLEAN NOT NULL DEFAULT true,
 revision INTEGER NOT NULL DEFAULT 1,
 last_status TEXT NOT NULL DEFAULT 'unchecked',
 last_checked_at TIMESTAMPTZ,
 last_http_status INTEGER,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
