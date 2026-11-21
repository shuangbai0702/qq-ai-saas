-- Legacy reservations are unknown: age alone is not proof of non-consumption.
ALTER TABLE platform_model_reservations ADD COLUMN dispatch_state TEXT NOT NULL DEFAULT 'unknown' CHECK(dispatch_state IN ('reserved','dispatched','unknown','receipt'));
ALTER TABLE platform_model_reservations ADD COLUMN provider_id TEXT;
ALTER TABLE platform_model_reservations ADD COLUMN dispatched_at TIMESTAMPTZ;
ALTER TABLE platform_model_reservations ADD COLUMN usage_tokens BIGINT CHECK(usage_tokens>=0);
ALTER TABLE platform_model_reservations ADD COLUMN diagnostic_code TEXT;
ALTER TABLE platform_model_reservations ADD CONSTRAINT platform_receipt_state CHECK((dispatch_state='receipt')=(usage_tokens IS NOT NULL));
