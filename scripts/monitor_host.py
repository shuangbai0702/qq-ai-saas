"""Read-only host collector. No Docker socket or host /proc mount in the API.

Run from a host timer every 30s with an output path inside the API's data volume.
Only the explicitly named service containers are inspected; no arguments or
environment variables are exported. Output is bounded and replaced atomically.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import time

SERVICES = {'fishbot-production-api': 'api', 'fishbot-production-agent': 'agent',
            'fishbot-production-pg': 'postgres', 'fishbot-production-gate': 'gate'}


def read_process(base, service, container, boot, hz):
    def identity():
        return base.joinpath('stat').read_text().rsplit(')', 1)[1].split()
    stat = identity()
    status = dict(line.split(':', 1) for line in base.joinpath('status').read_text().splitlines() if ':' in line)
    mem = {}
    try:
        for line in base.joinpath('smaps_rollup').read_text().splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                mem[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    cgroup = base.joinpath('cgroup').read_text()
    if container not in cgroup or identity()[19] != stat[19]:
        raise ValueError('lifetime-or-container-changed')
    # Container membership + an exact managed working directory also reveals
    # orphaned runtimes whose process record is missing. Never publish the path.
    owner = kind = None
    try:
        match = re.fullmatch(r'/var/lib/fishbot/bots/((?:group-)?[a-f0-9-]{36})/(ai|qq)(?:/.*)?', os.readlink(base / 'cwd'))
        if match and service in ('api', 'agent'):
            owner, kind = match[1], 'ai' if match[2] == 'ai' else 'protocol'
    except OSError:
        pass
    return dict(pid=int(base.name), namespacePid=int(status['NSpid'].split()[-1]),
                startTicks=stat[19],
                parentPid=int(stat[1]), name=status['Name'].strip()[:128],
                ownerHint=owner, kindHint=kind,
                created=boot + int(stat[19]) / hz, container=service,
                cgroup=cgroup.strip()[:512], pssBytes=mem.get('Pss'),
                ussBytes=(mem['Private_Clean'] + mem['Private_Dirty']) if 'Private_Clean' in mem and 'Private_Dirty' in mem else None,
                rssBytes=mem.get('Rss'), swapBytes=mem.get('SwapPss'),
                swapMetric='SwapPss', cpuSeconds=(int(stat[11]) + int(stat[12])) / hz)


def collect():
    result = dict(sampledAt=time.time(), processes=[], errors=[], truncated=False, scope='host-containers')
    inspected = json.loads(subprocess.check_output(['docker', 'inspect', *SERVICES], timeout=8))
    containers = {v['Id']: SERVICES[v['Name'].lstrip('/')] for v in inspected if v['State']['Running']}
    result['containers'] = [dict(name=v['Name'].lstrip('/'), running=v['State']['Running'],
                                 id=v['Id'][:12]) for v in inspected]
    boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime '))
    hz = os.sysconf('SC_CLK_TCK')
    for base in Path('/proc').iterdir():
        if not base.name.isdigit():
            continue
        try:
            cg = base.joinpath('cgroup').read_text()
            match = next(((cid, role) for cid, role in containers.items() if cid in cg), None)
            if not match:
                continue
            if len(result['processes']) >= 512:
                result['truncated'] = True
                break
            result['processes'].append(read_process(base, match[1], match[0], boot, hz))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, KeyError):
            if len(result['errors']) < 32:
                result['errors'].append(dict(pid=int(base.name), reason='host-process-unreadable'))
            else:
                result['truncated'] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    args = parser.parse_args()
    started = time.monotonic()
    try:
        result = collect()
    except (OSError, ValueError, subprocess.SubprocessError):
        result = dict(sampledAt=time.time(), processes=[], errors=[dict(reason='host-collector-unavailable')], scope='host-containers')
    result['collectDurationMs'] = round((time.monotonic() - started) * 1000, 2)
    output = Path(args.output)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, separators=(',', ':')), encoding='utf-8')
    os.chmod(temporary, 0o644)
    temporary.replace(output)


if __name__ == '__main__':
    main()
