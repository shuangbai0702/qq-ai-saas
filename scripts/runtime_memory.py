"""Owned-process idle reclamation; advice is not a memory quota."""
import ctypes
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import psutil

MIB = 1048576


def owned(row):
    process = psutil.Process(int(row['pid']))
    root = os.path.normcase(os.path.abspath(row['root']))
    if row.get('created') is None or not math.isfinite(float(row['created'])) or abs(process.create_time()-float(row['created'])) >= .01:
        raise ValueError('lifetime-mismatch')
    if not any((v := os.path.normcase(os.path.abspath(arg))) == root or v.startswith(root+os.sep) for arg in process.cmdline()):
        raise ValueError('owner-mismatch')
    return process


def owned_tree(row):
    """Track descendants through parentage and pin each process lifetime."""
    root = owned(row)
    result = [(root, root.create_time())]
    pending = [(child, child.create_time()) for child in root.children()]
    while pending:
        process, created = pending.pop()
        try:
            if not process.is_running() or abs(process.create_time()-created) >= .01:
                continue
            result.append((process, created))
            pending.extend((child, child.create_time()) for child in process.children())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    owned(row)
    return result


def ranges(text):
    """Only private, anonymous, resident, unlocked ranges; never shared binaries."""
    current = None
    for line in text.splitlines()+['00000000-00000000 ---p 00000000 00:00 0']:
        if re.match(r'^[0-9a-f]+-[0-9a-f]+ ', line):
            if current and current['private'] and current['inode'] == 0 and current['anon'] > 0 and current['rss'] > 0 and 'lo' not in current['flags']:
                yield current['start'], current['end']
            fields = line.split()
            start, end = [int(x, 16) for x in fields[0].split('-')]
            current = dict(start=start, end=end, private=fields[1].endswith('p'), inode=int(fields[4]), anon=0, rss=0, flags=[])
        elif current:
            if line.startswith('Anonymous:'): current['anon'] = int(line.split()[1])*1024
            elif line.startswith('Rss:'): current['rss'] = int(line.split()[1])*1024
            elif line.startswith('VmFlags:'): current['flags'] = line.split()[1:]


def reclaim(row, platform=sys.platform):
    tree = owned_tree(row)
    if platform == 'win32':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        api = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        api.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        api.EmptyWorkingSet.restype = wintypes.BOOL
        reclaimed, skipped = 0, 0
        for process, created in tree:
            handle = kernel.OpenProcess(0x0100 | 0x0400, False, process.pid)
            if not handle:
                skipped += 1
                continue
            try:
                if not process.is_running() or abs(process.create_time()-created) >= .01:
                    skipped += 1
                    continue
                if not api.EmptyWorkingSet(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                reclaimed += 1
            finally:
                kernel.CloseHandle(handle)
        owned(row)
        return {'strategy':'windows-idle-working-set-tree','processesReclaimed':reclaimed,'processesSkipped':skipped,'advisedVirtualBytes':None}
    if platform != 'linux': return {'strategy':'system-managed','reason':'unsupported-platform'}
    if psutil.swap_memory().total <= 0:
        return {'strategy':'system-managed','reason':'no-swap'}
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(os, 'pidfd_open') or not hasattr(libc, 'process_madvise'):
        return {'strategy':'system-managed','reason':'native-api-unavailable'}
    class IOVec(ctypes.Structure):
        _fields_ = [('base', ctypes.c_void_p), ('length', ctypes.c_size_t)]
    libc.process_madvise.argtypes = [ctypes.c_int, ctypes.POINTER(IOVec), ctypes.c_size_t, ctypes.c_int, ctypes.c_uint]
    libc.process_madvise.restype = ctypes.c_ssize_t
    budget, advised = 64*MIB, 0
    for process, created in tree:
        if budget <= 0: break
        fd = os.pidfd_open(process.pid, 0)
        try:
            if not process.is_running() or abs(process.create_time()-created) >= .01:
                continue
            for start, end in ranges(Path(f'/proc/{process.pid}/smaps').read_text()):
                length = min(end-start, budget)
                count = libc.process_madvise(fd, ctypes.byref(IOVec(start, length)), 1, 21, 0)
                if count < 0: raise OSError(ctypes.get_errno(), 'process_madvise unavailable or denied')
                advised += count
                budget -= length
                if budget <= 0: break
        finally: os.close(fd)
    owned(row)
    return {'strategy':'linux-idle-pageout-tree','processesScanned':len(tree),'advisedVirtualBytes':advised,'cgroupReclaim':reclaim_group(row)}


def reclaim_group(row):
    """Drop SwapCached only in a dedicated, owned leaf; never the host/API group."""
    try:
        owned(row)
        relative=next(line.split(':',2)[2] for line in Path(f"/proc/{row['pid']}/cgroup").read_text().splitlines() if line.startswith('0::'))
        base=Path('/sys/fs/cgroup').resolve()
        group=(base/relative.lstrip('/')).resolve()
        if group==base or not group.is_relative_to(base):return 'not-dedicated'
        if any(p.is_dir() for p in group.iterdir()):return 'not-leaf'
        pids=[int(x) for x in (group/'cgroup.procs').read_text().split()]
        if not pids:return 'empty'
        for pid in pids:
            process=psutil.Process(pid)
            owned({**row,'pid':pid,'created':process.create_time()})
        owned(row)
        (group/'memory.reclaim').write_text(str(64*MIB))
        return 'requested-64MiB-not-measured-freed'
    except OSError as error:
        return 'partial-or-denied-'+str(error.errno)
    except (ValueError,StopIteration,psutil.Error):return 'not-dedicated'


def decision(state, sample, idle=60):
    """Reclaim after 1min idle; one attempt per epoch, at least 5min apart."""
    now = sample['at']
    if state.get('created') != sample['created'] or now < state.get('at', now):
        state.clear()
        state.update(created=sample['created'], lastBusy=now, trimmedEpoch=None, nextAllowed=now+idle)
    dt = now-state.get('at', now)
    cpu = (sample['cpu']-state.get('cpu', sample['cpu']))/dt if dt > 0 else None
    if sample['busy'] or cpu is None or cpu < 0 or cpu > .02:
        state['lastBusy'] = now
    state.update(at=now, cpu=sample['cpu'])
    epoch = max(state['lastBusy'], sample['activity'])
    if sample['busy']: return 'active'
    if now-epoch < idle: return 'waiting-idle'
    if state.get('trimmedEpoch') == epoch: return 'already-reclaimed'
    if now < state['nextAllowed']: return 'cooldown'
    # Native reclaim APIs differ; the safety/pressure policy does not.
    if sample['available'] < 384*MIB: return 'reserve-protection'
    if sample['available'] > max(512*MIB, sample['total']*.35):
        return 'no-memory-pressure'
    return 'reclaim'


def activity(root, now):
    seen, busy = 0, False
    for file in [root/'memory-activity.json', root/'ai/memory-activity.json']:
        try:
            value = json.loads(file.read_text())
            stamp, created = float(value['at']), float(value['created'])
            if not math.isfinite(stamp) or not math.isfinite(created) or stamp > now+5 or stamp < created:
                continue
            source = psutil.Process(int(value['pid']))
            if abs(source.create_time()-created) >= .01:
                continue
            seen = max(seen, min(stamp, now))
            busy |= value.get('active') is True and now-stamp < 120
        except (OSError, ValueError, KeyError, TypeError, OverflowError, psutil.Error): pass
    return seen, busy


def sweep(rows, enabled=True):
    output, acted = [], False
    for row in rows:
        if row.get('kind') not in ('ai', 'protocol'): continue
        root = Path(row['root'])
        file = root/(row['kind']+'.memory.json')
        result = {'id': row['id'], 'kind': row['kind'], 'strategy': 'system-managed'}
        try:
            def current_record():
                rec=json.loads((root/(row['kind']+'.process.json')).read_text())
                if rec.get('pid')!=row['pid'] or rec.get('created')!=row.get('created'):
                    raise ValueError('process-record-replaced')
            current_record()
            process = owned(row)
            try: state = json.loads(file.read_text())
            except (OSError, ValueError): state = {}
            now = time.time()
            seen, busy = activity(root, now)
            mem = psutil.virtual_memory()
            sample = dict(at=now, created=process.create_time(), cpu=sum(process.cpu_times()[:2]), activity=seen, busy=busy, available=mem.available, total=mem.total, platform=sys.platform)
            action = decision(state, sample)
            if not enabled: action = 'disabled'
            if action == 'reclaim' and acted: action = 'staggered'
            result.update(decision=action, sampledAt=now)
            if action == 'reclaim':
                current_record()
                state['nextAllowed'] = now+300
                acted = True
                try:
                    result.update(reclaim(row))
                    if result['strategy'] != 'system-managed': state['trimmedEpoch'] = max(state['lastBusy'], seen)
                except (OSError, ValueError, psutil.Error) as error:
                    result.update(reason='native-operation-denied', errno=getattr(error, 'errno', None))
            current_record()
            owned(row)
            state['status'] = result
            temp = file.with_suffix('.tmp')
            temp.write_text(json.dumps(state))
            temp.replace(file)
        except (OSError, ValueError, KeyError, psutil.Error):
            result.update(decision='skipped', reason='owner-or-metrics-unavailable')
        output.append(result)
    return output


if __name__ == '__main__':
    print(json.dumps(sweep(json.load(sys.stdin), '--disabled' not in sys.argv)))
