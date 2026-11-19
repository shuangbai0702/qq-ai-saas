ALTER TABLE platform_model_credits ADD COLUMN campaign_id TEXT;
ALTER TABLE platform_model_credits ADD COLUMN campaign_claimed_at TIMESTAMPTZ;
ALTER TABLE platform_model_credits ADD COLUMN campaign_expires_at TIMESTAMPTZ;
ALTER TABLE platform_model_credits ADD COLUMN campaign_granted BIGINT NOT NULL DEFAULT 0 CHECK(campaign_granted>=0);
ALTER TABLE platform_model_credits ADD COLUMN campaign_spent BIGINT NOT NULL DEFAULT 0 CHECK(campaign_spent>=0);
ALTER TABLE platform_model_credits ADD COLUMN campaign_reserved BIGINT NOT NULL DEFAULT 0 CHECK(campaign_reserved>=0);
ALTER TABLE platform_model_reservations ADD COLUMN campaign_tokens INTEGER NOT NULL DEFAULT 0 CHECK(campaign_tokens>=0 AND campaign_tokens<=tokens);
