"""Bounded host-side MADV_PAGEOUT for one explicitly pinned process lifetime."""
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import sys
import time


def start_time(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def ranges(pid):
    current = None
    for line in Path(f'/proc/{pid}/smaps').read_text().splitlines()+['0000-0000 ---p 00000000 00:00 0']:
        if re.match(r'^[0-9a-f]+-[0-9a-f]+ ', line):
            if current and current['private'] and current['anonymous'] and current['rss'] and not current['locked']:
                yield current['start'], current['end']
            fields = line.split()
            start, end = [int(x, 16) for x in fields[0].split('-')]
            current = dict(start=start, end=end, private=fields[1].endswith('p') and fields[4]=='0', anonymous=0, rss=0, locked=False)
        elif current:
            if line.startswith('Anonymous:'):
                current['anonymous'] = int(line.split()[1])
            elif line.startswith('Rss:'):
                current['rss'] = int(line.split()[1])
            elif line.startswith('VmFlags:'):
                current['locked'] = 'lo' in line.split()[1:]


def pageout(pid, created, group, cursor=0):
    control = Path('/var/lib/fishbot-memory-control')
    if (control/'disabled').exists():
        raise RuntimeError('Reclamation disabled')
    if time.time()-(control/'guard-heartbeat').stat().st_mtime > 5:
        raise RuntimeError('Independent guard unavailable')
    relative = next(x.split(':', 2)[2] for x in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if x.startswith('0::'))
    if relative != group or not re.fullmatch(r'/system.slice/docker-[a-f0-9]+\.scope/fishbot-[a-z-]+(?:/runtime-[0-9]+-[0-9]+)?', group):
        raise RuntimeError('Cgroup ownership mismatch')
    libc = ctypes.CDLL(None, use_errno=True)
    class Vec(ctypes.Structure):
        _fields_ = [('base', ctypes.c_void_p), ('length', ctypes.c_size_t)]
    libc.process_madvise.argtypes = [ctypes.c_int, ctypes.POINTER(Vec), ctypes.c_size_t, ctypes.c_int, ctypes.c_uint]
    libc.process_madvise.restype = ctypes.c_ssize_t
    fd = os.pidfd_open(pid)
    advised = 0
    next_cursor = cursor
    budget = 64*1048576
    chunk = 4*1048576
    started = time.monotonic()
    try:
        if start_time(pid) != created:
            raise RuntimeError('Process lifetime mismatch')
        spans = list(ranges(pid))
        # Continue through large mappings over successive attempts; do not
        # repeatedly spend the budget on the same beginning of the heap.
        if not any(end > cursor for _, end in spans):
            cursor = 0
        for start, end in spans:
            start = max(start, cursor)
            while start < end and budget > 0:
                if time.monotonic()-started > 3 or (control/'disabled').exists() or (control/('activity-wake-'+Path(group).name)).exists():
                    return {'pid': pid, 'created': created, 'advisedVirtualBytes': advised,
                            'nextCursor': next_cursor, 'durationMs': round((time.monotonic()-started)*1000, 2)}
                if start_time(pid) != created:
                    raise RuntimeError('Process lifetime changed')
                current_group = next(x.split(':', 2)[2] for x in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if x.startswith('0::'))
                if current_group != group or time.time()-(control/'guard-heartbeat').stat().st_mtime > 5:
                    raise RuntimeError('Reclamation ownership or guard changed')
                length = min(end-start, chunk, budget)
                count = libc.process_madvise(fd, ctypes.byref(Vec(start, length)), 1, 21, 0)
                if count < 0:
                    code = ctypes.get_errno()
                    if code not in (errno.EINVAL, errno.EFAULT):
                        raise OSError(code, 'process_madvise failed')
                else:
                    advised += count
                start += length
                next_cursor = start
                budget -= length
            if budget <= 0:
                break
    finally:
        os.close(fd)
    return {'pid': pid, 'created': created, 'advisedVirtualBytes': advised,
            'nextCursor': next_cursor, 'durationMs': round((time.monotonic()-started)*1000, 2)}


if __name__ == '__main__':
    print(json.dumps(pageout(int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv)>4 else 0)))
