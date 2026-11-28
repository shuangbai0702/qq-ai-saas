ALTER TABLE tenants DROP CONSTRAINT tenants_bot_limit_check;
ALTER TABLE tenants ADD CONSTRAINT tenants_bot_limit_check CHECK(bot_limit BETWEEN 1 AND 999);
