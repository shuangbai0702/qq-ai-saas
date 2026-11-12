"""Supervise a website-only encrypted origin bridge; never start a FishBot Agent."""
import argparse
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import time
import urllib.request
import urllib.parse


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(config):
    ipaddress.ip_address(config['sshHost'])
    if not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', config['sshUser']):
        raise ValueError('invalid SSH user')
    port = config['localPort']
    if type(port) is not int or not 1024 <= port <= 65535 or port in (5432, 8787):
        raise ValueError('invalid bridge port')
    dns_port = config.get('dnsTlsBridgePort')
    if dns_port is not None and (type(dns_port) is not int or not 1024 <= dns_port <= 65535 or dns_port in (5432, 8787, port)):
        raise ValueError('invalid DNS TLS bridge port')
    if not re.fullmatch('[a-z0-9.-]+', config['httpHost']):
        raise ValueError('invalid HTTP host')
    if config.get('mode', 'encrypted-origin') not in ('encrypted-origin', 'local-api-trial'):
        raise ValueError('invalid website mode')
    if config.get('mode') == 'local-api-trial' and not Path(config.get('preflightConfig', '')).is_absolute():
        raise ValueError('explicit preflight configuration required')
    for key in ('sourceRoot', 'stateRoot', 'ssh', 'privateKey', 'knownHosts', 'cloudflared', 'dnsConfig'):
        if not Path(config[key]).is_absolute():
            raise ValueError('absolute paths required')


def ssh_command(config):
    validate(config)
    command = [config['ssh'], '-N', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+config['knownHosts'],
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=3', '-o', 'ConnectTimeout=10',
            '-i', config['privateKey'], '-L', '127.0.0.1:'+str(config['localPort'])+':127.0.0.1:80']
    if config.get('dnsTlsBridgePort'):
        command += ['-L', '127.0.0.1:'+str(config['dnsTlsBridgePort'])+':www.duckdns.org:443']
    return command + [config['sshUser']+'@'+config['sshHost']]


def publish_via_tls_bridge(port, domain, token, url):
    """Keep DuckDNS TLS/SNI verification across a pinned SSH transport."""
    class Connection(http.client.HTTPSConnection):
        def connect(self):
            sock = socket.create_connection(('127.0.0.1', port), timeout=self.timeout)
            try:
                self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname='www.duckdns.org')
            except BaseException:
                sock.close()
                raise
    connection = Connection('www.duckdns.org', timeout=10)
    try:
        connection.request('GET', '/update?'+urllib.parse.urlencode(dict(domains=domain, token=token, txt=url)))
        response = connection.getresponse()
        return response.status == 200 and response.read(64).decode('ascii', errors='replace').strip() == 'OK'
    finally:
        connection.close()


def upstream_port(config):
    # Trial mode is explicit: it must never be an implicit failure fallback.
    return 8787 if config.get('mode') == 'local-api-trial' else config['localPort']


def origin_healthy(config):
    req = urllib.request.Request('http://127.0.0.1:'+str(upstream_port(config))+'/v1/health',
                                 headers={'Host': config['httpHost']})
    try:
        if config.get('mode') == 'local-api-trial':
            preflight = json.loads(Path(config['preflightConfig']).read_text(encoding='utf-8-sig'))
            state = json.loads((Path(preflight['stateRoot'])/'supervisor.json').read_text())
            if Path(preflight['executionAuthority']).exists() or state.get('preflight') is not True:
                return False
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=3) as response:
            value = json.loads(response.read(8193))
            return response.status == 200 and value.get('ok') is True and value.get('publicOrigin') == 'https://'+config['httpHost'] and (config.get('mode') != 'local-api-trial' or value.get('agent') is False)
    except (OSError, ValueError, AttributeError, KeyError):
        return False


def run(config):
    validate(config)
    source, state = Path(config['sourceRoot']), Path(config['stateRoot'])
    state.mkdir(parents=True, exist_ok=True)
    supervisor = load('web_owned_supervisor', source/'scripts/windows-production-supervisor.py')
    dns = load('web_dns_sync', source/'scripts/tunnel-txt-sync.py')
    dns_config = json.loads(Path(config['dnsConfig']).read_text(encoding='utf-8-sig'))
    dns.validate(dns_config)
    import msvcrt
    with (state/'bridge.lock').open('a+b') as lock:
        lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        job = supervisor.WindowsJob(); job.assign()
        ssh = supervisor.Service('web-ssh', ssh_command(config), source, dict(os.environ), state, state, 2)
        tunnel = supervisor.Service('web-tunnel', [config['cloudflared'], 'tunnel', '--url',
            'http://127.0.0.1:'+str(upstream_port(config)), '--http-host-header', config['httpHost'], '--no-autoupdate'],
            source, dict(os.environ), state, state, 3)
        last_sync = 0
        supervisor.atomic(state/'bridge.json', {'pid': os.getpid(), 'created': supervisor.psutil.Process().create_time(), 'websiteOnly': True})
        try:
            while True:
                stop = state/'bridge.stop'
                try:
                    if int(stop.read_text().strip()) == os.getpid():
                        stop.unlink(); break
                except (OSError, ValueError):
                    pass
                now = time.monotonic()
                ssh.reap(now); tunnel.reap(now)
                if ssh.process is None and now >= ssh.next:
                    ssh.start()
                healthy = ssh.process is not None and origin_healthy(config)
                if healthy and tunnel.process is None and now >= tunnel.next:
                    tunnel.start()
                status = 'origin-unhealthy'
                if healthy and tunnel.process is not None:
                    status = 'ready-not-published'
                    if now-last_sync >= 15:
                        last_sync = now
                        try:
                            update = dns.publish if not config.get('dnsTlsBridgePort') else lambda domain, token, url: publish_via_tls_bridge(config['dnsTlsBridgePort'], domain, token, url)
                            status = dns.sync_once(dns_config, health=lambda: origin_healthy(config), update=update)
                        except Exception:
                            status = 'dns-check-or-update-failed'
                        supervisor.atomic(state/'dns-status.json', {'status': status, 'checkedAt': time.time()})
                supervisor.atomic(state/'readiness.json', {'websiteOnly': True, 'originHealthy': healthy,
                    'tunnelRunning': tunnel.process is not None, 'businessAgentAuthorized': False, 'checkedAt': time.time()})
                time.sleep(1)
        finally:
            tunnel.stop(); ssh.stop()
            supervisor.atomic(state/'bridge-stopped.json', {'pid': os.getpid(), 'treesExited': True})
            job.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    try:
        run(json.loads(args.config.read_text(encoding='utf-8-sig')))
    except Exception:
        print('Website bridge failed; no business execution authority changed', flush=True)
        raise SystemExit(1)
