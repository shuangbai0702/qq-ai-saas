CREATE TABLE referral_codes (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  code TEXT NOT NULL UNIQUE
);

CREATE TABLE referral_rewards (
  invitee_id TEXT PRIMARY KEY REFERENCES users(id),
  inviter_id TEXT NOT NULL REFERENCES users(id),
  rewarded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (invitee_id <> inviter_id)
);
CREATE INDEX referral_rewards_inviter ON referral_rewards(inviter_id);

CREATE TABLE platform_model_credits (
  tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
  granted BIGINT NOT NULL DEFAULT 0 CHECK (granted >= 0),
  spent BIGINT NOT NULL DEFAULT 0 CHECK (spent >= 0),
  reserved BIGINT NOT NULL DEFAULT 0 CHECK (reserved >= 0)
);

CREATE TABLE platform_model_reservations (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES platform_model_credits(tenant_id),
  bot_id TEXT NOT NULL REFERENCES bots(id),
  tokens INTEGER NOT NULL CHECK (tokens > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
