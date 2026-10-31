# Linux Memory Optimization Record (2026-10-31)

## Scope and result

Applied host-level persistent swap/zswap tuning to the production Linux host. The running FishBot container was not restarted or modified. API, Agent, PostgreSQL migration and container splitting were not deployed in this pass.

Original host snapshot: MemTotal 1,817,884 kB, MemAvailable 473,812 kB, no swap, `vm.swappiness=0`, zswap disabled, max pool 20%, lzo/zbud. The source was sampled at different times and under different load, so it is not a controlled before/after comparison.

Current persistent settings:

- `/swapfile`: 2 GiB, mode 0600, enabled and registered in `/etc/fstab`.
- `vm.swappiness=200`, persisted by `/etc/sysctl.d/99-fishbot-memory.conf` and `fishbot-memory-tuning.service` (runs after `systemd-sysctl.service`, because the existing `/etc/sysctl.conf` resets swappiness to 0).
- zswap enabled, 25% pool limit, lzo compressor and zbud pool, persisted by `fishbot-zswap.service`.
- No production `memory.high` change. All QQ processes are in one platform cgroup; a low host cgroup threshold would constrain API, AI and QQ together.

Rollback files are in `/root/fishbot-memory-rollback-20261031-015556/`. The state JSON records the original sysctl and zswap values. `rollback-all.sh` passed `sh -n`; it has not been executed. It disables the two units, removes the swap fstab entry/configs, restores original swappiness/zswap settings, swaps off and removes `/swapfile`.

## Isolated cold-page experiments

Tests ran in temporary, memory-limited systemd service cgroups, never in the FishBot container cgroup. All test units were stopped and removed afterwards.

- Single 64 MiB anonymous workload: cgroup memory fell from about 32 MiB to 1.1 MiB after `memory.reclaim`; swap rose by about 30 MiB; memory contents verified intact; touch/restore took 637 ms. Test-cgroup OOM counters remained 0. Host PSI avg10 remained 0 for this sample; zswap pool was about 20.9 MiB.
- Three concurrent 64 MiB workloads: each cgroup was reclaimed to roughly 1.7-2.5 MiB resident with about 68 MiB swap. After raising each `memory.high` from 32 MiB to 96 MiB and waking all workers, all contents verified intact; individual touch times were 1,509, 1,521 and 1,591 ms. Test-cgroup OOM counters were 0. During the short concurrent restore, host MemAvailable fell to 302,480 kB and PSI avg10 peaked at 3.98; platform cgroup `high`, `oom` and `oom_kill` counters remained 0. Within the following observation window MemAvailable returned to about 554 MiB and PSI avg10 to 0.24, then later to 0.00.
- An earlier 192 MiB probe was stopped before its touch phase; it is recorded as incomplete, not a pass. Its test unit was cleaned up.

These synthetic anonymous-memory timings are not QQ message P95. They do not verify production QQ cold-page restoration or zero message loss under memory pressure.

## Production observations after tuning

The production container remained healthy with restart count 0. Its cgroup `memory.events` showed `oom=0`, `oom_kill=0`, `high=0`; cgroup PSI avg10 was 0 in the final sample. One 20-request local `/v1/health` sample measured p50 1.86 ms, p95 5.12 ms, max 27.06 ms. This is a health endpoint measurement, not message latency.

A real, explicitly authorized private test message to the configured owner UIN returned OneBot HTTP 200, retcode 0 and a message id. The existing real NapCat bot was separately observed online before that send; post-send container health remained healthy. Delivery/read receipt and end-to-end model response latency were not measured.

A later host snapshot showed MemTotal 1,817,884 kB, MemAvailable 608,832 kB, SwapTotal 2,097,148 kB, SwapFree 1,989,296 kB, SwapCached 6,052 kB, Zswap 34,096 kB and Zswapped 98,912 kB. The debug zswap pool reported 34,914,304 bytes. These values confirm use of the configured swap/zswap path, but differing load and time mean the MemAvailable delta cannot be attributed to the tuning alone.

Approximate per-process sample from `/proc/*/smaps_rollup` inside the production container (PSS, USS=Private_Clean+Private_Dirty):

| Process | PSS | USS | Swap |
|---|---:|---:|---:|
| API Node | 265 MiB | 254 MiB | 3.5 MiB |
| Dedicated AstrBot | 154 MiB | 152 MiB | 5.4 MiB |
| Shared AstrBot | 190 MiB | 188 MiB | 9.1 MiB |
| NapCat | 271 MiB | 260 MiB | 0.3 MiB |
| Mock protocol worker | 36 MiB | 31 MiB | 0.7 MiB |
| Application PID total | 916 MiB | 886 MiB | 19.1 MiB |

These owners are not five independent QQ accounts: one live NapCat QQ, one shared AI group, one dedicated AI instance and a Mock worker were present. Shared AI is counted once. `docker stats` and the sum of process PSS use different accounting boundaries and must not be added together.

## Capacity estimate and limits

Ten low-activity QQ were not tested. The measured live NapCat process alone used about 260 MiB USS in this snapshot. Ten copies at that point-in-time rate would be roughly 2.5 GiB private memory before API/PostgreSQL/shared AI and kernel overhead, so ten real NapCat accounts are not supported by current evidence on a 1.73 GiB host. Idle swap may improve density, but the experiment did not measure ten protocol runtimes or sustained message traffic. A defensible stable QQ count and incremental per-QQ production cost remain unknown; do not advertise ten-account capacity.

## Verification

Passed after current code changes: focused migration export, login lifecycle (including cancelled late-online and refresh/stop barrier), and recovery tests (3/3); `tsc --noEmit`. Earlier in this task production build and compiled smoke passed before the final late-online guard change; the current source was typechecked but the production build was not rerun after that final guard.

Not completed: real PostgreSQL import/rollback/restore, production database cutover, separate API/Agent deployment, API-only restart with QQ online, Agent recovery while API is down, ten-account capacity, real QQ under induced host pressure, message P95<=5s, and a controlled whole-machine before/after memory comparison.
