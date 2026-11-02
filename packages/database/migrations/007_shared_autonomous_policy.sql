-- Both modes use bot_config_revisions.config.behavior.groupParticipationGroups.
-- Invalidate old permits and preserve goals, receipts, usage and account data.
-- Change only the former default interval and retain custom sleep intervals.
WITH changed AS (
 UPDATE autonomy_settings
 SET config=(config-'groupAllowlist') ||
     CASE WHEN config->>'minSleepSeconds'='900' THEN '{"minSleepSeconds":600}'::jsonb ELSE '{}'::jsonb END,
     epoch=epoch+1,state=CASE WHEN config->>'mode'='active' THEN 'ready' ELSE 'disabled' END,
     wait='{}',lease_owner=NULL,lease_until=NULL,last_error=NULL,
     next_wake_at=CASE WHEN config->>'mode'='active' THEN now() ELSE NULL END,updated_at=now()
 WHERE config ? 'groupAllowlist' OR config->>'minSleepSeconds'='900'
 RETURNING bot_id,tenant_id,epoch,config
), canceled AS (
 UPDATE autonomy_actions a SET status='canceled',finished_at=now()
 FROM changed s WHERE a.bot_id=s.bot_id AND a.tenant_id=s.tenant_id AND a.status='planned'
), cleared AS (
 DELETE FROM autonomy_wakes w USING changed s WHERE w.bot_id=s.bot_id AND w.tenant_id=s.tenant_id
)
INSERT INTO autonomy_wakes(bot_id,tenant_id,dedupe_key,epoch,due_at,reason)
SELECT bot_id,tenant_id,'shared-policy:'||epoch,epoch,now(),'shared-policy-updated'
FROM changed WHERE config->>'mode'='active';
