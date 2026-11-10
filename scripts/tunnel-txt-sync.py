"""Publish a gated QuickTunnel URL to DuckDNS TXT; credentials stay private."""
import argparse
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

HEALTH_URL = 'http://127.0.0.1:8787/v1/health'
UPDATE_URL = 'https://www.duckdns.org/update'
TUNNEL = re.compile(r'(?<![A-Za-z0-9_./:@-])https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com(?![A-Za-z0-9_.:/?@#%+-])')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def validate(config):
    if config.get('healthUrl', HEALTH_URL) != HEALTH_URL:
        raise ValueError('Only the fixed local health endpoint is allowed')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', config.get('domain','')):
        raise ValueError('Invalid single DuckDNS domain label')
    if not isinstance(config.get('authorityValue'),str) or not config['authorityValue'].strip():
        raise ValueError('Authority value is required')
    for key in ('tokenFile','logFile','authorityFile','routeEnabledFile','stateFile'):
        if not isinstance(config.get(key),str) or not Path(config[key]).is_absolute():
            raise ValueError('Private file paths must be absolute')


def gates(config):
    try:
        expected = config['authorityValue']
        return all(Path(config[key]).read_text(encoding='utf-8').strip() == expected for key in ('authorityFile','routeEnabledFile'))
    except (OSError,ValueError):
        return False


def latest_url(file):
    with Path(file).open('rb') as stream:
        stream.seek(0,2);size=stream.tell();offset=max(0,size-2*1024*1024)
        stream.seek(offset)
        if offset:stream.readline()
        text=stream.read(2*1024*1024).decode('utf-8',errors='replace')
    values=list(TUNNEL.finditer(text))
    return values[-1].group(0) if values else None


def healthy():
    try:
        with opener().open(HEALTH_URL,timeout=3) as response:
            value=json.loads(response.read(8193))
            return response.status==200 and value.get('ok') is True
    except (OSError,ValueError,AttributeError):
        return False


def publish(domain, token, url):
    query=urllib.parse.urlencode(dict(domains=domain,token=token,txt=url))
    # Never print the request, response body or URL-bearing urllib exceptions.
    with opener().open(UPDATE_URL+'?'+query,timeout=10) as response:
        return response.status==200 and response.read(64).decode('ascii',errors='replace').strip()=='OK'


def sync_once(config, health=healthy, update=publish):
    validate(config)
    if not gates(config):return 'gated'
    if not health():return 'unhealthy'
    if not gates(config):return 'gated'
    url=latest_url(config['logFile'])
    if not url:return 'no-url'
    file=Path(config['stateFile'])
    try:
        previous=json.loads(file.read_text(encoding='utf-8'))
        if previous.get('url')==url and previous.get('domain')==config['domain'] and previous.get('authorityValue')==config['authorityValue']:
            return 'unchanged'
    except (OSError,ValueError,AttributeError):pass
    token=Path(config['tokenFile']).read_text(encoding='utf-8').strip()
    if not token or any(char.isspace() for char in token):return 'invalid-token'
    # Recheck after all file/I/O boundaries immediately before the external write.
    if not gates(config):return 'gated'
    if not update(config['domain'],token,url):return 'update-rejected'
    file.parent.mkdir(parents=True,exist_ok=True)
    temporary=file.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(domain=config['domain'],url=url,authorityValue=config['authorityValue'],updatedAt=time.time())),encoding='utf-8')
    temporary.replace(file)
    return 'updated'


def run(config):
    validate(config)
    lock_file=Path(config['stateFile']).with_suffix('.lock')
    lock_file.parent.mkdir(parents=True,exist_ok=True)
    with lock_file.open('a+b') as lock:
        if os.name=='nt':
            import msvcrt
            lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            started=time.monotonic()
            try:status=sync_once(config)
            except Exception:status='check-or-update-failed'
            print('TXT sync: '+status,flush=True)
            time.sleep(max(0,60-(time.monotonic()-started)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    args=parser.parse_args()
    try:run(json.loads(args.config.read_text(encoding='utf-8')))
    except Exception:
        print('TXT sync: startup-failed',flush=True)
        raise SystemExit(1)
