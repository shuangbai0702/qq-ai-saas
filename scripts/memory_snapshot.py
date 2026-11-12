"""Read-only, bounded snapshot of API and lifetime-validated managed processes."""
import json
import math
import os
from pathlib import Path
import sys
import time

import psutil

# runtime_memory has a __main__ guard; importing it never runs sweep/reclaim.
from runtime_memory import owned_tree

MAX_DIRECTORIES = 256
MAX_PROCESSES = 512
MAX_ERRORS = 32
MAX_INPUT = 65536
MAX_RECORD = 16384


def number(value):
    try:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def lifetime(process, created):
    # A fresh Process avoids the cached create_time on the original instance.
    current = psutil.Process(process.pid)
    if not process.is_running() or abs(current.create_time() - created) >= .01:
        raise ValueError('lifetime-mismatch')


def metrics(process, created, owner, kind):
    lifetime(process, created)
    row = dict(pid=process.pid, parentPid=None, name=None, ownerId=owner,
               kind=kind, created=created, rssBytes=None, pssBytes=None,
               ussBytes=None, swapBytes=None, vmsBytes=None, cpuSeconds=None,
               threads=None, readBytes=None, writeBytes=None, privateCommitBytes=None)
    # Independent reads retain RSS when full-info or another metric is denied.
    def read(method):
        try:
            return method()
        except (psutil.AccessDenied, AttributeError, NotImplementedError):
            return None
    row['parentPid'] = number(read(process.ppid))
    name = read(process.name)
    if isinstance(name, str):
        row['name'] = name.replace('\\', '/').rsplit('/', 1)[-1][:128]
    basic = read(process.memory_info)
    for key, attr in [('rssBytes', 'rss'), ('vmsBytes', 'vms')]:
        row[key] = number(getattr(basic, attr, None))
    if sys.platform == 'win32':
        # PrivateUsage is commit backed by RAM or pagefile, not disk residency.
        # New psutil versions expose the same Windows metric as vms only.
        row['privateCommitBytes'] = number(getattr(basic, 'private', getattr(basic, 'vms', None)))
    full = read(lambda: process.memory_full_info())
    for key, attr in [('pssBytes', 'pss'), ('ussBytes', 'uss'), ('swapBytes', 'swap')]:
        row[key] = number(getattr(full, attr, None))
    cpu = read(process.cpu_times)
    if cpu is not None:
        row['cpuSeconds'] = number(cpu.user + cpu.system)
    row['threads'] = number(read(process.num_threads))
    io = read(lambda: process.io_counters())
    row['readBytes'] = number(getattr(io, 'read_bytes', None))
    row['writeBytes'] = number(getattr(io, 'write_bytes', None))
    lifetime(process, created)
    return row


def windows_compression():
    """Physical working set of the kernel compression process; never infer zero."""
    result = dict(residentBytes=None, status='not-observed', source='compression-process-working-set')
    if sys.platform != 'win32':
        result['status'] = 'not-applicable'
        return result
    started = time.monotonic()
    try:
        for count, process in enumerate(psutil.process_iter(['name'])):
            if count >= 2048 or time.monotonic() - started > 2:
                result['status'] = 'scan-incomplete'
                break
            if str(process.info.get('name') or '').casefold() not in ('memory compression', 'memcompression'):
                continue
            created = process.create_time()
            lifetime(process, created)
            # The native compression process belongs to System, not an app.
            if process.ppid() != 4:
                continue
            value = number(process.memory_info().rss)
            lifetime(process, created)
            result.update(residentBytes=value, status='ok' if value is not None else 'metric-unavailable')
            break
    except (psutil.Error, OSError, ValueError, NotImplementedError) as exc:
        result['status'] = 'lifetime-mismatch' if isinstance(exc, ValueError) else 'access-denied' if isinstance(exc, psutil.AccessDenied) else 'metric-unavailable'
    return result


def collect(request):
    started = time.monotonic()
    result = dict(platform=sys.platform, processes=[], errors=[], truncated=False)
    seen = set()

    def error(owner, kind, reason):
        if len(result['errors']) < MAX_ERRORS:
            result['errors'].append(dict(ownerId=owner, kind=kind, reason=reason))
        else:
            result['truncated'] = True

    def reason(exc):
        if isinstance(exc, psutil.NoSuchProcess):
            return 'process-exited'
        if isinstance(exc, (psutil.AccessDenied, PermissionError)):
            return 'access-denied'
        if isinstance(exc, ValueError) and str(exc) in ('lifetime-mismatch', 'owner-mismatch'):
            return str(exc)
        return 'record-or-metrics-unavailable'

    def add(process, created, owner, kind):
        if process.pid in seen:
            return
        if len(result['processes']) >= MAX_PROCESSES:
            result['truncated'] = True
            return
        try:
            result['processes'].append(metrics(process, created, owner, kind))
            seen.add(process.pid)
        except (psutil.Error, OSError, ValueError) as exc:
            error(owner, kind, reason(exc))

    try:
        if not isinstance(request, dict) or not isinstance(request.get('root'), str) or not request['root']:
            raise ValueError()
        root = Path(request['root']).resolve()
        pid = request.get('apiPid')
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError()
    except (OSError, ValueError, TypeError):
        error(None, 'collector', 'invalid-input')
        result['collectDurationMs'] = round((time.monotonic() - started) * 1000, 3)
        return result

    # apiPid is explicitly supplied by the caller, not discovered from records.
    try:
        process = psutil.Process(pid)
        add(process, process.create_time(), None, 'api')
    except (psutil.Error, OSError, ValueError) as exc:
        error(None, 'api', reason(exc))

    count = 0
    def directories(base, groups_only=False):
        nonlocal count
        try:
            with os.scandir(base) as entries:
                for entry in entries:
                    if groups_only and not entry.name.startswith('group-'):
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if count >= MAX_DIRECTORIES:
                        result['truncated'] = True
                        return
                    count += 1
                    yield Path(entry.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            error(None, 'collector', reason(exc))

    def scan(directory):
        owner = directory.name
        for kind in ('ai', 'protocol'):
            file = directory / (kind + '.process.json')
            try:
                if file.is_symlink():
                    raise ValueError()
                with file.open('r', encoding='utf-8') as stream:
                    text = stream.read(MAX_RECORD + 1)
                if len(text) > MAX_RECORD:
                    raise ValueError()
                record = json.loads(text)
                if not isinstance(record, dict):
                    raise ValueError()
                record_pid = record.get('pid')
                created = number(record.get('created'))
                if not isinstance(record_pid, int) or isinstance(record_pid, bool) or record_pid <= 0 or created is None:
                    raise ValueError()
                row = dict(pid=record_pid, created=created, root=str(directory))
                tree = owned_tree(row)
                # Revalidate the owner after discovery before publishing descendants.
                lifetime(psutil.Process(record_pid), created)
                for process, born in tree:
                    add(process, born, owner, kind)
            except FileNotFoundError:
                continue
            except (psutil.Error, OSError, ValueError, TypeError, KeyError) as exc:
                error(owner, kind, reason(exc))

    for directory in directories(root / 'bots'):
        scan(directory)
    for directory in directories(root, groups_only=True):
        scan(directory)

    host = {}
    for method, fields in [
        (psutil.virtual_memory, [('totalBytes', 'total'), ('availableBytes', 'available'),
                                ('freeBytes', 'free'), ('cachedBytes', 'cached'), ('buffersBytes', 'buffers')]),
        (psutil.swap_memory, [('swapTotalBytes', 'total'), ('swapUsedBytes', 'used'),
                             ('swapInBytes', 'sin'), ('swapOutBytes', 'sout')]),
    ]:
        try:
            info = method()
        except (psutil.Error, OSError, NotImplementedError):
            info = None
        for key, attr in fields:
            host[key] = number(getattr(info, attr, None))
    result['host'] = host
    result['collectDurationMs'] = round((time.monotonic() - started) * 1000, 3)
    return result


def main():
    try:
        text = sys.stdin.read(MAX_INPUT + 1)
        request = json.loads(text) if len(text) <= MAX_INPUT else None
    except (ValueError, OSError):
        request = None
    print(json.dumps(collect(request), ensure_ascii=True, allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
