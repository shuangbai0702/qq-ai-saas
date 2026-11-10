"""Windows adapter for the host idle-memory policy; working-set targets are soft.

Run alongside windows-memory-guard.py under an external service supervisor.
Neither component terminates runtimes or enforces a committed-memory quota.
"""
import argparse
import json
import math
import os
from pathlib import Path
import time

import psutil
from runtime_memory import MIB, activity, owned_tree, reclaim

IDLE_SECONDS = float(os.environ.get('FISHBOT_MEMORY_IDLE_SECONDS', '18'))
RECLAIM_SECONDS = float(os.environ.get('FISHBOT_MEMORY_RECLAIM_SECONDS', '40'))
ROLE_MIB = {'napcat': 48, 'shared-ai': 72, 'dedicated-ai': 24, 'control-api': 128, 'agent': 128, 'postgres': 128, 'mock': int(os.environ.get('FISHBOT_MEMORY_IDLE_MIB', '128'))}


def atomic(file, value):
    temp = file.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.replace(file)


def target(role):
    key = 'FISHBOT_MEMORY_'+role.upper().replace('-', '_')+'_MIB'
    value = int(os.environ.get(key, str(ROLE_MIB[role])))
    if value <= 0:
        raise ValueError('Memory target must be positive')
    return value*MIB


def delay(before, after, failures):
    failures = failures+1 if before-after < MIB else 0
    return min(600, RECLAIM_SECONDS*2**min(failures, 4)), failures


def restore(base, root, now):
    try:
        value = json.loads((base/'reclaim-state.json').read_text(encoding='utf-8'))
        if value['root'] != str(root) or value['boot'] != psutil.boot_time():
            return {}
        state = {}
        for row in value['processes']:
            key = (int(row['pid']), float(row['created']))
            stamp = float(row['next'])
            if not math.isfinite(stamp) or not 0 <= stamp <= now+600:
                continue
            state[key] = dict(cpu=0, busy=now, next=stamp, failures=max(0,min(16,int(row['failures']))))
        return state
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return {}


def persist(base, root, state):
    atomic(base/'reclaim-state.json', dict(root=str(root), boot=psutil.boot_time(),
        processes=[dict(pid=key[0], created=key[1], next=value['next'], failures=value['failures']) for key,value in state.items()]))


def services(base):
    """Supervisor-owned records: absolute argv owner root + PID + create_time."""
    for role in ('control-api', 'agent', 'postgres'):
        file = base/'services'/(role+'.process.json')
        if not file.exists():
            continue
        if file.is_symlink() or file.stat().st_size > 16384:
            raise ValueError('Invalid service record')
        row = json.loads(file.read_text(encoding='utf-8'))
        if not isinstance(row.get('root'), str) or not Path(row['root']).is_absolute():
            raise ValueError('Service root must be an absolute argv owner')
        row.update(recordFile=str(file), id=role, kind=role)
        yield row, role, owned_tree(row)


def discover(root):
    """Read only managed records; never enumerate unrelated machine processes."""
    directories = list((root/'bots').iterdir()) if (root/'bots').exists() else []
    directories += list(root.glob('group-*'))
    if len(directories) > 256:
        raise RuntimeError('Too many runtime directories')
    for directory in directories:
        if not directory.is_dir() or directory.is_symlink():
            continue
        for kind in ('ai', 'protocol'):
            try:
                file = directory/(kind+'.process.json')
                if file.is_symlink() or file.stat().st_size > 16384:
                    continue
                row = json.loads(file.read_text(encoding='utf-8'))
                row.update(root=str(directory.resolve()), id=directory.name, kind=kind)
                tree = owned_tree(row)
                role = ('shared-ai' if directory.name.startswith('group-') else 'dedicated-ai') if kind == 'ai' else 'napcat'
                if kind == 'protocol' and not any('napcat' in arg.lower() for arg in tree[0][0].cmdline()):
                    role = 'mock'
                yield row, role, tree
            except (OSError, ValueError, TypeError, KeyError, psutil.Error):
                continue


def step(row, role, tree, state, now, native=reclaim):
    key = (row['pid'], row['created'])
    cpu = sum(sum(process.cpu_times()[:2]) for process, _ in tree)
    rss = sum(process.memory_info().rss for process, _ in tree)
    previous = state.get(key)
    if previous is None:
        previous = state[key] = dict(cpu=cpu, busy=now, next=now, failures=0)
    if cpu < previous['cpu'] or cpu-previous['cpu'] > .2:
        previous['busy'] = now
    previous['cpu'] = cpu
    stamp, busy = activity(Path(row['root']), now) if role in ('shared-ai', 'dedicated-ai') else (0, False)
    if busy or now-stamp < IDLE_SECONDS:
        previous['busy'] = now
    result = dict(pid=row['pid'], created=row['created'], role=role, target=target(role), current=rss, strategy='windows-idle-working-set-tree')
    if now-previous['busy'] < IDLE_SECONDS:
        result['decision'] = 'active'
    elif now < previous['next']:
        result['decision'] = 'cooldown'
    elif rss <= result['target']:
        result['decision'] = 'below-target'
    else:
        # Revalidate the record immediately before advice, not just its PID.
        record = Path(row['recordFile']) if row.get('recordFile') else Path(row['root'])/(row['kind']+'.process.json')
        current = json.loads(record.read_text(encoding='utf-8'))
        if current.get('pid') != row['pid'] or current.get('created') != row['created']:
            raise ValueError('process-record-replaced')
        previous['next'] = now+RECLAIM_SECONDS
        try:
            result.update(native(row, 'win32'), decision='reclaim')
            after = sum(process.memory_info().rss for process, _ in owned_tree(row))
        except (OSError, ValueError, psutil.Error) as error:
            result.update(decision='reclaim-failed', reason=type(error).__name__)
            after = rss
        interval, previous['failures'] = delay(rss, after, previous['failures'])
        previous['next'] = now+interval
        result.update(after=after, nextAllowed=previous['next'], futileAttempts=previous['failures'])
    return result


def run(root, base):
    if os.name != 'nt':
        raise RuntimeError('Windows memory controller requires Windows')
    base.mkdir(parents=True, exist_ok=True)
    # Windows byte-range locking releases ownership when the service dies.
    import msvcrt
    with (base/'controller.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        state = restore(base, root, time.time())
        while True:
            guard = base/'guard-heartbeat'
            if not guard.exists() or time.time()-guard.stat().st_mtime > 5:
                raise RuntimeError('Independent guard is not healthy')
            if (base/'disabled').exists():
                for value in state.values():
                    value['busy'] = time.time()
                time.sleep(1)
                continue
            now = time.time()
            rows, live = [], set()
            for row, role, tree in [*services(base), *discover(root)]:
                if (base/'disabled').exists() or time.time()-guard.stat().st_mtime > 5:
                    break
                live.add((row['pid'], row['created']))
                try:
                    rows.append(step(row, role, tree, state, now))
                except (OSError, ValueError, TypeError, KeyError, psutil.Error):
                    continue
            state = {key: value for key, value in state.items() if key in live}
            persist(base, root, state)
            atomic(base/'controller-state.json', dict(at=now, groups=rows, quota='working-set-target-only'))
            (base/'armed').touch()
            time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    run(args.root.resolve(), args.state.resolve())
