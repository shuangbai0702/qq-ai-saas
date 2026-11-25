-- Observations are scheduler progress, never a second copy of chat history.
-- Keep the existing event/version fences and actual outcome counters.
-- Goals, Work, tool/unknown receipts and Journal are untouched.
WITH slots AS (
 SELECT bot_id,'current' AS slot,observation AS value FROM autonomy_settings
 UNION ALL
 SELECT bot_id,'feedback',observation->'latestFeedback' FROM autonomy_settings
 WHERE jsonb_typeof(observation->'latestFeedback')='object'
), cleaned AS (
 SELECT bot_id,slot,
 COALESCE((SELECT jsonb_object_agg(key,val) FROM jsonb_each(CASE WHEN jsonb_typeof(value)='object' THEN value ELSE '{}'::jsonb END) AS kv(key,val)
 WHERE key IN ('at','eventId','scope','senderId','owner','directed','phase')),'{}'::jsonb)
 || CASE WHEN jsonb_typeof(value->'result')='object' THEN jsonb_build_object('result',
 COALESCE((SELECT jsonb_object_agg(key,val) FROM jsonb_each(value->'result') AS kv(key,val)
 WHERE key IN ('status','confirmedMessages','protocolAttempts','protocolConfirmed')),'{}'::jsonb)) ELSE '{}'::jsonb END AS value
 FROM slots
)
UPDATE autonomy_settings AS s SET observation=current.value
 || CASE WHEN feedback.value IS NOT NULL THEN jsonb_build_object('latestFeedback',feedback.value) ELSE '{}'::jsonb END
FROM cleaned AS current LEFT JOIN cleaned AS feedback ON feedback.bot_id=current.bot_id AND feedback.slot='feedback'
WHERE current.slot='current' AND s.bot_id=current.bot_id;
