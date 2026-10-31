# Frozen production snapshot copy: PostgreSQL and independent Agent integration

## Scope and build

Executed against copies only on 2026-10-31. No Git commands, production restart/switch, live PGlite access, host memory changes, compose changes or entrypoint changes were performed in this task.

Server-only esbuild completed at **2026-10-31T02:33:18.007Z** from the current workspace control-api, node-agent, packages, adapters and migration CLI. Target node22 ESM; migrations copied alongside output; no Vite build. Staged output runs on Node24 in the existing `fishbot/qq-ai-saas:0.1.0-security-r3` image. This verifies that build, not later concurrent Agent changes.

Frozen archive `/root/fishbot-pre-reclaim-20261031-101753/data.tar` SHA256 was checked before and after:

`01f8b9504700ceb79e36521ae20e2be8c9a0c6aa658d94250ffdbe563c9b4b5d`

Copied `restore` to `/opt/fishbot-snapshot-integration/source`; only that new source was opened, using the existing export CLI lease. No lock removal was needed. Original tar and restore directory were not opened as databases or changed.

## Migration evidence

Export/import used `node dist-server/scripts/migrate-pglite-postgres.js export|import /stage/snapshot.json`. Export ran with `--network none`; import ran on the internal network. Snapshot checksum:

`a307752878e6800f1059ef3cb7f749255478071647ba095e5f0087920ca05fb8`

All 16 tables passed independent post-import content comparison. Combined canonical content SHA256:

`b944e942f7ce76db667b1d1aea3daba46ec3a91ade8f99af1cf4ddec033bde5d`

| Table | Rows |
| --- | ---: |
| users | 9 |
| tenants | 9 |
| memberships | 9 |
| sessions | 31 |
| runtime_groups | 1 |
| bots | 10 |
| bot_config_revisions | 42 |
| secrets | 3 |
| instances | 10 |
| qq_bindings | 3 |
| login_sessions | 37 |
| commands | 121 |
| invitations | 6 |
| invitation_redemptions | 8 |
| group_members | 3 |
| audit_events | 48 |

Source audit sequence was 48/called; import transaction verified restart at 49/uncalled. All **3** secret ciphertexts decrypted with the original Key using tenant:bot AAD. No plaintext, Key, identities or personal content were logged. Detailed per-table hashes/counts/match results are in protected remote `migration-evidence.json`.

## Actual API/Agent runtime integration

An unmodified imported database `snapshot_original` was dumped with `pg_dump -Fc` and restored to separate `snapshot_integration` with `pg_restore --exit-on-error`. Before starting services, a transaction set original bots/groups/members stopped, emptied health, terminated login sessions, canceled pending/processing commands and cleared instance heartbeat. Added one synthetic mock fixture and login command only.

Network `fishbot-snapshot-internal` was verified `internal=true`; neither API nor Agent published ports or attached to production networks. PostgreSQL inspection showed `5432/tcp:null` (no host listener), despite an initial localhost publish request; services communicate solely over the internal network.

Actual independent containers started from staged compiled main.js entries. Initial Agent launch failed because `/app/references/astrbot/.venv/bin/python` was absent. Only the isolated env was corrected to the observed `/usr/local/bin/python3`; the synthetic failed login command/session were reset and retried. No production data command was replayed.

Runtime assertions passed:

- API `/v1/health`: `{ok:true,database:"postgresql",agent:false}`.
- Independent Agent consumed the synthetic login command, completed it and started an actual mock protocol worker.
- All 10 original bots remained stopped with empty health; no real QQ login or message was attempted.
- Restarted **only** `fishbot-snapshot-api` with `docker restart`.
- Independent Agent container ID `2573df4a7e312888912fd178b179f66591a9c120f2cd25758cd6608ab763b2a9`, StartedAt `2026-10-31T02:41:15.137377261Z`, RestartCount 0 were identical before/after API restart.
- Mock PID and process creation time were unchanged; process_control identity validation returned valid and authenticated mock HTTP `/state` still responded.

This exercises real PG + independent Agent + API and a real mock child. It does not exercise AstrBot AI/model responses or real QQ operation.

Health script now accepts PID 1, checks `/proc/PID/cmdline` for the compiled independent Agent entry, and requires the existing lease heartbeat within 20 seconds. Remote checks passed for healthy Agent and rejected API PID impersonation and a two-minute-stale lease. Lease freshness does **not** prove business tick progress; a business heartbeat and exact process creation record remain a release condition for the main Agent implementation.

## Retained evidence and final state

All three new containers are **stopped and retained**: `fishbot-snapshot-api`, `fishbot-snapshot-agent`, `fishbot-snapshot-pg`. Volume `fishbot-snapshot-pg` retained at `/var/lib/docker/volumes/fishbot-snapshot-pg/_data`; databases `snapshot_original` and `snapshot_integration` remain separate. Network `fishbot-snapshot-internal` retained. Previous synthetic drill resources were untouched.

Protected directory `/opt/fishbot-snapshot-integration` (700) retains:

- original source copy, original Key copy, snapshot JSON;
- `snapshot-original.dump` before runtime sanitization and `integration-after.dump` before shutdown;
- staged compiled output and helper code;
- `migration-evidence.json`, `integration-before.json`, `integration-after.json`;
- isolated env/Key files. These contain sensitive data and are not embedded in this report.

Production final observation: running, healthy, RestartCount **0**, StartedAt `2026-10-30T16:29:35.511848572Z`.

## Minimum production switch conditions (not executed)

1. Complete business heartbeat/process-identity contract and rebuild/retest that exact final API/Agent output; current runtime evidence applies to the timestamp above.
2. During an authorized write freeze, take a new current production snapshot and original Key backup; this frozen snapshot can no longer represent later writes.
3. Import into an empty production PG target, verify schema, all-table canonical hashes/counts, decryption count and audit next ID; retain untouched rollback data/dump.
4. Configure final PG credentials, shared Key/runtime-volume ownership and single independent Agent ownership. Use the actual Python executable/source/runtime paths and required resource quotas.
5. Stop the embedded owner during the controlled cutover so no second Agent/QQ owner exists; start external Agent and API with EXTERNAL_AGENT=true only after explicit release authorization. Do not run QQ from a snapshot clone alongside the live owner.
6. Verify actual production health/login continuity after switch and define rollback handling for any writes made after PG takes ownership.
