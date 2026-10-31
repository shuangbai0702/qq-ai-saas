"""Host-root cgroup-v2 controller. An independent guard must be active first.

Soft limits govern reclaim/throttling, not guaranteed physical residency. This
controller never sets memory.max, kills processes, or changes QQ login state.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import time

import signal
BASE = Path('/var/lib/fishbot-memory-control')
CONTAINER = os.environ.get('FISHBOT_CONTAINER', 'fishbot-current-platform')
IDLE_SECONDS = float(os.environ.get('FISHBOT_MEMORY_IDLE_SECONDS', '18'))
IDLE_MIB = int(os.environ.get('FISHBOT_MEMORY_IDLE_MIB', '96'))
DATA = Path(os.environ.get('FISHBOT_HOST_DATA', '/var/lib/docker/volumes/fishbot-saas-security-r3_fishbot-data/_data'))
RECLAIM_SECONDS = float(os.environ.get('FISHBOT_MEMORY_RECLAIM_SECONDS', '40'))
EXTRA_CONTAINERS = json.loads(os.environ.get('FISHBOT_MEMORY_EXTRA_CONTAINERS', '{}'))


def container_scope(state):
    if not state['State']['Running'] or not state['State']['Pid']:
        raise RuntimeError('Managed container stopped')
    relative = next(line.split(':', 2)[2] for line in Path(f"/proc/{state['State']['Pid']}/cgroup").read_text().splitlines() if line.startswith('0::'))
    group = Path('/sys/fs/cgroup'+relative)
    if group.name.startswith('fishbot-'):
        group = group.parent
    if not group.name.startswith('docker-') or not group.name.endswith('.scope'):
        raise RuntimeError('Unexpected container cgroup')
    return group


def atomic(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.replace(path)


def process(pid):
    root = Path(f'/proc/{pid}')
    fields = root.joinpath('stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'ppid': int(fields[1]), 'created': fields[19],
            'cpu': (int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK'),
            'argv': root.joinpath('cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')}


def classify(row, parents):
    argv = row['argv']
    if 'shared-worker.py' in argv:
        return 'shared-ai'
    if 'headless.py' in argv or 'worker.py' in argv or 'astrbot' in argv and 'python' in argv:
        return 'dedicated-ai'
    if 'runner.mjs' in argv:
        return 'napcat'
    if 'mock' in argv.lower():
        return 'mock'
    return parents.get(row['ppid'], 'api')


def activity(rows):
    """Accept telemetry only from a live, lifetime-matched owned process.

    Completed requests count too: active->inactive may happen between polls.
    NSpid maps telemetry's container PID to the host PID used by the controller.
    """
    newest = 0
    busy = False
    now = time.time()
    identities = {}
    try:
        boot = int(next(line.split()[1] for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime ')))
    except (OSError, StopIteration, ValueError):
        return newest, busy
    for row in rows:
        try:
            status = Path(f"/proc/{row['pid']}/status").read_text()
            namespace_pid = int(next(line.split()[-1] for line in status.splitlines() if line.startswith('NSpid:')))
            identities[namespace_pid] = boot + int(row['created']) / os.sysconf('SC_CLK_TCK')
        except (OSError, StopIteration, ValueError, KeyError):
            continue
    files = list(DATA.glob('bots/*/memory-activity.json')) + list(DATA.glob('bots/*/ai/memory-activity.json'))
    for file in files:
        try:
            value = json.loads(file.read_text())
            created = identities.get(int(value['pid']))
            stamp = float(value['at'])
            recorded_created = float(value['created'])
            if created is None or not math.isfinite(recorded_created) or abs(created-recorded_created) > .05:
                continue
            if not math.isfinite(stamp) or stamp > now+5 or stamp < created:
                continue
            newest = max(newest, min(stamp, now))
            busy |= value.get('active') is True and now-stamp < 120
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            continue
    return newest, busy


def activity_wakes(rows, owners, now):
    """Only actual AI owners consume AI telemetry; other components use CPU."""
    signals = {name: activity([row for row in rows if owners[row['pid']] == name])
               for name in ('shared-ai', 'dedicated-ai')}
    wakes = {name: busy or now-stamp < IDLE_SECONDS for name, (stamp, busy) in signals.items()}
    return wakes


def resident_anon(pid):
    try:
        return int(next(line.split()[1] for line in Path(f'/proc/{pid}/status').read_text().splitlines() if line.startswith('RssAnon:')))*1024
    except (OSError, StopIteration, ValueError):
        return None


def reclaim_delay(before, after, failures):
    """No useful resident reduction: back off instead of advising cold pages."""
    failures = failures+1 if before is None or after is None or before-after < 1024*1024 else 0
    return min(600, RECLAIM_SECONDS * 2**min(failures, 4)), failures


def main():
    BASE.mkdir(mode=0o700, exist_ok=True)
    import fcntl
    lock = (BASE/'controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    guard = BASE/'guard-heartbeat'
    if not guard.exists() or time.time()-guard.stat().st_mtime > 5:
        raise RuntimeError('Independent guard is not healthy')
    state = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER], text=True))[0]
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
    identity = state['Id']
    pid = state['State']['Pid']
    parent = container_scope(state)
    names = ['api', 'shared-ai', 'dedicated-ai', 'napcat', 'mock']
    groups = {name: parent/('fishbot-'+name) for name in names}
    for group in groups.values():
        group.mkdir(exist_ok=True)
    extra_states = {name: json.loads(subprocess.check_output(['docker', 'inspect', container], text=True, timeout=3))[0]
                    for name, container in EXTRA_CONTAINERS.items()}
    if set(extra_states) & set(groups):
        raise RuntimeError('External container group names must be unique')
    external_groups = {name: container_scope(value) for name, value in extra_states.items()}
    if len(set(external_groups.values()) | {parent}) != len(external_groups)+1:
        raise RuntimeError('Managed container cgroups overlap')
    # First evacuate the domain node, as required by cgroup v2's no-internal-
    # process rule. The hierarchy remains under Docker's original scope.
    for value in (parent/'cgroup.procs').read_text().split():
        try:
            (groups['api']/'cgroup.procs').write_text(value)
        except (ProcessLookupError, FileNotFoundError):
            pass
    reclaimed = {}
    futile = {}
    task = None
    task_owner = None
    (parent/'cgroup.subtree_control').write_text('+memory')
    groups.update(external_groups)
    names = list(groups)
    atomic(BASE/'limits.json', [{'path': str(group/'memory.high')} for group in groups.values()])
    previous = {}
    last_busy = {name: time.time() for name in names}
    iteration = 0
    try:
        while not (BASE/'disabled').exists():
            if time.time()-guard.stat().st_mtime > 5:
                raise RuntimeError('Independent guard heartbeat expired')
            if iteration % 10 == 0:
                current = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER], text=True, timeout=3))[0]
                if current['Id'] != identity or current['State']['Pid'] != pid or current['State']['StartedAt'] != state['State']['StartedAt']:
                    raise RuntimeError('Container lifetime changed')
                for name, container in EXTRA_CONTAINERS.items():
                    current_extra = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True, timeout=3))[0]
                    old_extra = extra_states[name]
                    if current_extra['Id'] != old_extra['Id'] or current_extra['State']['StartedAt'] != old_extra['State']['StartedAt']:
                        raise RuntimeError('External container lifetime changed')
            (BASE/'armed').touch()
            rows = []
            external_owners = {}
            for name, group in groups.items():
                process_files = [group/'cgroup.procs']
                if name in external_groups:
                    process_files += list(group.glob('**/cgroup.procs'))
                for process_file in set(process_files):
                    for value in process_file.read_text().split():
                        try:
                            row = process(int(value))
                            rows.append(row)
                            if name in external_groups:
                                external_owners[row['pid']] = name
                        except (OSError, ValueError, IndexError):
                            pass
            # Process parentage establishes complete group ownership, including
            # descendants whose argv does not repeat the root worker command.
            owners = {}
            for _ in range(len(rows)+1):
                changed = False
                for row in rows:
                    name = external_owners.get(row['pid']) or classify(row, owners)
                    if owners.get(row['pid']) != name:
                        owners[row['pid']] = name
                        changed = True
                if not changed:
                    break
            now = time.time()
            # Shared-group traffic must not keep unrelated dedicated AI warm.
            # Only the process that owns a telemetry file can wake its AI group.
            wakes = activity_wakes(rows, owners, now)
            for row in rows:
                name = owners[row['pid']]
                old = previous.get(row['pid'])
                if not old or old['created'] != row['created'] or row['cpu']-old['cpu'] > .2:
                    last_busy[name] = now
                # Confirm lifetime immediately before migrating a live process.
                try:
                    if name not in external_groups and process(row['pid'])['created'] == row['created']:
                        (groups[name]/'cgroup.procs').write_text(str(row['pid']))
                except (OSError, ValueError, IndexError):
                    pass
            for name, group in groups.items():
                marker = BASE/('activity-wake-'+group.name)
                if wakes.get(name, False):
                    marker.touch()
                else:
                    marker.unlink(missing_ok=True)
            if task is not None:
                if task.poll() is not None:
                    delay, failures = reclaim_delay(task_before, resident_anon(task_owner[0]), futile.get(task_owner, 0))
                    futile[task_owner] = failures
                    reclaimed[task_owner] = now+delay
                    with (BASE/'pageout-results.jsonl').open('a') as file:
                        file.write(json.dumps({'at': now, 'owner': task_owner, 'exit': task.returncode, 'nextDelay': delay, 'futileAttempts': failures})+'\n')
                    task = None
                elif wakes.get(owners.get(task_owner[0]), False):
                    task.terminate()
                elif now-task_started > 5:
                    task.kill()
            if task is None:
                for row in rows:
                    name = owners[row['pid']]
                    key = (row['pid'], row['created'])
                    if name not in external_groups and not wakes.get(name, False) and now-last_busy[name] >= IDLE_SECONDS and now >= reclaimed.get(key, 0):
                        task_before = resident_anon(row['pid'])
                        if task_before is None or task_before < 4*1048576:
                            reclaimed[key] = now+RECLAIM_SECONDS
                            continue
                        group = str(groups[name]).removeprefix('/sys/fs/cgroup')
                        log = (BASE/'pageout-output.jsonl').open('a')
                        try:
                            task = subprocess.Popen(['/usr/bin/python3', str(Path(__file__).with_name('linux-pageout.py')), str(row['pid']), row['created'], group], stdout=log, stderr=log)
                        finally:
                            log.close()
                        task_owner = key
                        task_started = now
                        reclaimed[key] = now+RECLAIM_SECONDS
                        break
            limits = {}
            for name, group in groups.items():
                default_mib = {'napcat': 48, 'shared-ai': 48, 'dedicated-ai': 16}.get(name, IDLE_MIB)
                idle_mib = int(os.environ.get('FISHBOT_MEMORY_'+name.upper().replace('-', '_')+'_MIB', str(default_mib)))
                value = 'max' if wakes.get(name, False) or now-last_busy[name] < IDLE_SECONDS else str(idle_mib*1048576)
                file = group/'memory.high'
                if file.read_text().strip() != value:
                    file.write_text(value)
                limits[name] = {'high': value, 'current': int((group/'memory.current').read_text()),
                                'swap': int((group/'memory.swap.current').read_text())}
            atomic(BASE/'controller-state.json', {'at': now, 'container': identity, 'groups': limits})
            if iteration % 10 == 0:
                with (BASE/'controller.jsonl').open('a') as file:
                    file.write(json.dumps({'at': now, 'groups': limits})+'\n')
            previous = {row['pid']: row for row in rows}
            live = {(row['pid'], row['created']) for row in rows}
            reclaimed = {key: value for key, value in reclaimed.items() if key in live}
            futile = {key: value for key, value in futile.items() if key in live}
            iteration += 1
            time.sleep(1)
    finally:
        if task is not None and task.poll() is None:
            try:
                task.kill()
                task.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass  # Never skip soft-limit relaxation because helper cleanup failed.
        for group in groups.values():
            try:
                (group/'memory.high').write_text('max')
            except OSError:
                pass
        (BASE/'armed').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
