"""Read-only Windows host packet for the existing memory monitor contract."""
import argparse
import json
import os
from pathlib import Path
import time

import psutil
from runtime_memory import owned
from memory_snapshot import metrics, lifetime


def record(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        value = json.loads(stream.read(16385))
    if not isinstance(value, dict):
        raise ValueError('invalid-record')
    return value


def collect(config):
    state = Path(config['stateRoot'])
    result = dict(sampledAt=time.time(), platform='win32', processes=[],
                  errors=[], truncated=False, expectedAbsent=[])
    try:
        supervisor = record(state/'supervisor.json')
        supervisor_owner = {**supervisor, 'root': str(Path(config['sourceRoot']))}
        owned(supervisor_owner)
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        result['errors'].append(dict(reason='supervisor-owner-unavailable'))
        return result
    seen = set()
    for service, container, owner in (
        ('control-api', 'api', config['sourceRoot']),
        ('postgres', 'postgres', config['postgresData']),
        ('agent', 'agent', config['sourceRoot']),
    ):
        try:
            path = state/'services'/(service+'.process.json')
            if service == 'agent' and not path.exists() and supervisor.get('preflight') is True:
                result['expectedAbsent'].append('agent:preflight')
                continue
            rec = record(path)
            row = {**rec, 'root': str(Path(owner))}
            process = owned(row)
            pending = [(process, rec['created'])]
            while pending:
                if len(seen) >= 512 or time.time()-result['sampledAt'] > 10:
                    result['truncated'] = True
                    break
                current, created = pending.pop()
                identity = (current.pid, created)
                if identity in seen:
                    continue
                lifetime(current, created)
                item = metrics(current, created, None, container)
                item.update(container=container, namespacePid=current.pid, kindHint=container)
                result['processes'].append(item)
                seen.add(identity)
                for child in current.children():
                    pending.append((child, child.create_time()))
            # Do not publish descendants after root ownership/record changed.
            owned(row)
            if record(path) != rec:
                raise ValueError('record-changed')
        except (OSError, ValueError, KeyError, TypeError, psutil.Error):
            result['processes'] = [p for p in result['processes'] if p['container'] != container]
            result['errors'].append(dict(kind=container, reason='owner-or-metrics-unavailable'))
    try:
        owned(supervisor_owner)
        if record(state/'supervisor.json') != supervisor:
            raise ValueError('supervisor-changed')
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        result['processes'] = []
        result['errors'].append(dict(reason='supervisor-changed'))
    return result


def publish(config):
    result = collect(config)
    target = Path(config['dataRoot'])/'monitor-host.json'
    temp = target.with_name('monitor-host.'+str(os.getpid())+'.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(result, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = record(args.config)
    import msvcrt
    with (Path(config['stateRoot'])/'host-monitor.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        while True:
            publish(config)
            if args.once:
                return
            time.sleep(20)


if __name__ == '__main__':
    main()
