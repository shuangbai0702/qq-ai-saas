"""Independent host guard: relax registered soft limits on unhealthy ownership/pressure."""
import json
import os
from pathlib import Path
import subprocess
import time

BASE = Path('/var/lib/fishbot-memory-control')
CONTAINERS = os.environ.get('FISHBOT_GUARD_CONTAINERS', 'fishbot-current-platform').split(',')


def recovery_count(old, now):
    """A cooldown is not healthy running time. Only sustained armed time resets."""
    since = old.get('healthySince')
    healthy = isinstance(since, (int, float)) and float(old.get('at', now)) <= since <= now and now-since >= 600
    return 1 if healthy else max(1, min(6, int(old.get('count', 0))+1))


def mark_healthy(now):
    try:
        path = BASE/'recovery.json'
        value = json.loads(path.read_text())
        value['healthySince'] = now
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(value))
        temp.replace(path)
    except (OSError, ValueError, TypeError):
        pass


def rollback(reason):
    marker = BASE / 'disabled'
    if marker.exists():
        return
    marker.write_text(reason)
    now = time.time()
    try:
        old = json.loads((BASE/'recovery.json').read_text())
        count = recovery_count(old, now)
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        count = 1
    try:
        (BASE/'recovery.json').write_text(json.dumps({'at': now, 'count': count, 'cooldown': min(600, 30*2**(count-1))}))
    except OSError:
        pass  # Recovery bookkeeping must never prevent limit relaxation.
    try:
        registry = json.loads((BASE / 'limits.json').read_text())
    except (OSError, ValueError):
        registry = []
    failures = []
    for row in registry:
        path = Path(row['path'])
        if path.is_relative_to('/sys/fs/cgroup') and path.name == 'memory.high':
            try:
                if path.read_text().strip() != 'max':
                    path.write_text('max')
            except FileNotFoundError:
                pass
            except OSError:
                failures.append(str(path))
    with (BASE / 'guard.jsonl').open('a') as file:
        file.write(json.dumps({'at': time.time(), 'rollback': reason, 'failedPaths': failures}) + '\n')


def pressure_exceeded(samples, now, total):
    """Five full-stall seconds in the rolling ~20-second observation window."""
    if samples and (now < samples[-1][0] or total < samples[-1][1]):
        samples.clear()
    samples.append((now, total))
    while len(samples) > 1 and now - samples[1][0] >= 20:
        samples.pop(0)
    return now - samples[0][0] >= 18 and total - samples[0][1] > 5_000_000


def inspect_container(name):
    state = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True, timeout=3))[0]
    if not state['State']['Running'] or not state['State']['Pid']:
        raise RuntimeError('Managed container stopped')
    pid = state['State']['Pid']
    relative = next(line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::'))
    group = Path('/sys/fs/cgroup' + relative)
    if group.name.startswith('fishbot-'):
        group = group.parent
    if not group.name.startswith('docker-') or not group.name.endswith('.scope'):
        raise RuntimeError('Unexpected container scope')
    events = dict(line.split() for line in (group / 'memory.events').read_text().splitlines())
    return (state['Id'], state['State']['StartedAt'], pid, events.get('oom', '0'), events.get('oom_kill', '0'))

def main():
    BASE.mkdir(mode=0o700, exist_ok=True)
    mark_healthy(None)  # A guard restart cannot certify an unobserved interval.
    recovered_since = None
    previous = None
    bad_health = 0
    samples = []
    healthy_since = None
    while True:
        try:
            identities = {name: inspect_container(name) for name in CONTAINERS}
            reason = None
            if previous is not None and identities != previous:
                reason = 'container-lifetime-or-oom-changed'
            previous = identities
            now = time.time()
            if (BASE / 'trigger').exists():
                reason = 'controlled-trigger'
                (BASE / 'trigger').unlink()
            if not (BASE / 'disabled').exists() and (BASE / 'armed').exists() and now - (BASE / 'armed').stat().st_mtime > 15:
                reason = 'controller-heartbeat-expired'
            full = next(line for line in Path('/proc/pressure/memory').read_text().splitlines() if line.startswith('full '))
            total = int(dict(field.split('=') for field in full.split()[1:])['total'])
            if pressure_exceeded(samples, now, total):
                reason = 'host-memory-full-stall-over-5s-in-20s'
            try:
                subprocess.run(['curl', '-fsS', '--max-time', '2', 'http://127.0.0.1:8091/v1/health'],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                bad_health = 0
            except (OSError, subprocess.SubprocessError):
                bad_health += 1
            if bad_health >= 2:
                reason = 'api-health-failed-twice'
            if reason:
                healthy_since = None
                recovered_since = None
                rollback(reason)
            elif (BASE / 'disabled').exists():
                healthy_since = None
                recovered_since = recovered_since or now
                try:
                    cooldown = max(30, min(600, float(json.loads((BASE/'recovery.json').read_text())['cooldown'])))
                except (OSError, ValueError, KeyError, TypeError):
                    cooldown = 30
                if now - recovered_since >= cooldown:
                    (BASE / 'disabled').unlink(missing_ok=True)
                    recovered_since = None
            elif (BASE/'armed').exists() and now-(BASE/'armed').stat().st_mtime <= 15:
                if healthy_since is None:
                    healthy_since = now
                    mark_healthy(now)
            else:
                if healthy_since is not None:
                    mark_healthy(None)
                healthy_since = None
            (BASE / 'guard-heartbeat').write_text(str(time.time()))
        except Exception as error:
            recovered_since = None
            rollback('guard-check-error-' + type(error).__name__)
        time.sleep(2)


if __name__ == '__main__':
    main()
