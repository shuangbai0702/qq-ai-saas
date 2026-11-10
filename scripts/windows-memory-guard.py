"""Independent Windows idle-reclaim guard; native OOM restart belongs to services.

Windows has no cgroup PSI/full-stall or memory.events counters. Low available
memory, API failures and expired controller ownership suspend further advice.
Already trimmed working sets can grow immediately without limit relaxation.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

import psutil
from runtime_memory import owned

spec = importlib.util.spec_from_file_location('linux_guard_policy', Path(__file__).with_name('linux-memory-guard.py'))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def reason(base, now, available, bad_health):
    if available < 384*1048576:
        return 'host-available-below-reserve'
    if bad_health >= 2:
        return 'api-health-failed-twice'
    armed = base/'armed'
    if not (base/'disabled').exists() and armed.exists() and now-armed.stat().st_mtime > 15:
        return 'controller-heartbeat-expired'
    return None


def service_lifetimes(base):
    result = {}
    for role in ('control-api', 'agent', 'postgres'):
        file = base/'services'/(role+'.process.json')
        if (base/'services').exists() and not file.exists():
            raise ValueError('Required service owner unavailable')
        if file.exists():
            if file.is_symlink() or file.stat().st_size > 16384:
                raise ValueError('Invalid service record')
            row = json.loads(file.read_text(encoding='utf-8'))
            process = owned(row)
            result[role] = (process.pid, process.create_time())
    return result


def run(base, url):
    if os.name != 'nt':
        raise RuntimeError('Windows memory guard requires Windows')
    base.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (base/'guard.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        policy.BASE = base
        policy.mark_healthy(None)
        bad_health, recovered, healthy = 0, None, None
        previous_services = None
        while True:
            now = time.time()
            try:
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status != 200:
                            raise ValueError('API unhealthy')
                    bad_health = 0
                except (OSError, ValueError, urllib.error.URLError):
                    bad_health += 1
                why = reason(base, now, psutil.virtual_memory().available, bad_health)
                try:
                    current_services = service_lifetimes(base)
                    if previous_services is not None and current_services != previous_services:
                        why = 'service-lifetime-changed'
                    previous_services = current_services
                except (OSError, ValueError, TypeError, KeyError, psutil.Error):
                    why = 'service-owner-unavailable'
                if why:
                    recovered, healthy = None, None
                    # Shared cooldown accounting, without Linux limit writes.
                    if not (base/'disabled').exists():
                        try:
                            old = json.loads((base/'recovery.json').read_text())
                            count = policy.recovery_count(old, now)
                        except (OSError, ValueError, TypeError, AttributeError):
                            count = 1
                        (base/'disabled').write_text(why)
                        (base/'recovery.json').write_text(json.dumps(dict(at=now, count=count, cooldown=min(600, 30*2**(count-1)))))
                elif (base/'disabled').exists():
                    recovered = now if recovered is None else recovered
                    try:
                        value = json.loads((base/'recovery.json').read_text())
                        cooldown = max(30, min(600, float(value['cooldown'])))
                    except (OSError, ValueError, TypeError, KeyError):
                        cooldown = 30
                    if now-recovered >= cooldown:
                        (base/'disabled').unlink()
                        recovered = None
                elif (base/'armed').exists():
                    if healthy is None:
                        healthy = now
                        policy.mark_healthy(now)
                (base/'guard-heartbeat').touch()
            except Exception:
                # Fail closed: controller independently requires our heartbeat.
                recovered, healthy = None, None
            time.sleep(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--health-url', default='http://127.0.0.1:8787/v1/health')
    args = parser.parse_args()
    run(args.state.resolve(), args.health_url)
