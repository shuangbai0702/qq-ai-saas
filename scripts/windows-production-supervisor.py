"""Private-config Windows production supervisor with owned nested Job trees."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.request

import psutil


def atomic(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(file)


class WindowsJob:
    """Handles are never inherited; closing a service job kills its whole tree."""
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('Windows Job Objects are required')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.GetCurrentProcess.restype = wintypes.HANDLE
        self.api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenThread.restype = wintypes.HANDLE
        self.api.ResumeThread.argtypes = [wintypes.HANDLE]
        self.api.ResumeThread.restype = wintypes.DWORD
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.kill_on_close(True)
        except BaseException:
            self.api.CloseHandle(self.handle)
            raise

    def check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def kill_on_close(self, enabled):
        class Basic(ctypes.Structure):
            _fields_ = [('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),('flags',wintypes.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('activeLimit',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name,ctypes.c_ulonglong) for name in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
        class Limits(ctypes.Structure):
            _fields_ = [('basic',Basic),('io',Io),('processMemory',ctypes.c_size_t),('jobMemory',ctypes.c_size_t),('peakProcess',ctypes.c_size_t),('peakJob',ctypes.c_size_t)]
        value = Limits();value.basic.flags = 0x2000 if enabled else 0
        self.check(self.api.SetInformationJobObject(self.handle,9,ctypes.byref(value),ctypes.sizeof(value)))

    def assign(self, process=None):
        handle = self.api.GetCurrentProcess() if process is None else int(process._handle)
        self.check(self.api.AssignProcessToJobObject(self.handle,handle))

    def active(self):
        class Accounting(ctypes.Structure):
            _fields_ = [('user',ctypes.c_longlong),('kernel',ctypes.c_longlong),('periodUser',ctypes.c_longlong),('periodKernel',ctypes.c_longlong),('faults',wintypes.DWORD),('total',wintypes.DWORD),('active',wintypes.DWORD),('terminated',wintypes.DWORD)]
        value = Accounting()
        self.check(self.api.QueryInformationJobObject(self.handle,1,ctypes.byref(value),ctypes.sizeof(value),None))
        return value.active

    def terminate(self, timeout=15):
        self.check(self.api.TerminateJobObject(self.handle,1))
        end = time.monotonic()+timeout
        while self.active():
            if time.monotonic() >= end:
                raise RuntimeError('Owned Job tree did not exit')
            time.sleep(.05)

    def close(self):
        if self.handle:
            self.check(self.api.CloseHandle(self.handle));self.handle=None

    def resume(self, process):
        threads = psutil.Process(process.pid).threads()
        if len(threads) != 1:
            raise RuntimeError('Unexpected suspended process threads')
        handle = self.api.OpenThread(0x0002,False,threads[0].id)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if self.api.ResumeThread(handle) == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(handle)


class Service:
    def __init__(self, name, command, cwd, environment, state, owner, delay=10):
        self.name,self.command,self.cwd,self.environment = name,command,str(cwd),environment
        self.state,self.owner,self.delay = state,str(owner),delay
        self.process,self.job,self.reader = None,None,None
        self.next = 0

    def record(self):
        return self.state/'services'/(self.name+'.process.json')

    def start(self):
        job = WindowsJob()
        try:
            process = subprocess.Popen(self.command,cwd=self.cwd,env=self.environment,
                creationflags=0x00000004 | 0x00000200 | 0x08000000,
                close_fds=True,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            self.process,self.job=process,job
            # Bind before any user code or child process can execute.
            job.assign(process)
            created = psutil.Process(process.pid).create_time()
            if self.name in ('control-api','agent','postgres'):
                atomic(self.record(),dict(pid=process.pid,created=created,root=self.owner))
            self.reader=threading.Thread(target=self.capture,daemon=True);self.reader.start()
            job.resume(process)
        except BaseException as error:
            if self.process is not None:
                self.process.kill();self.process.wait(timeout=10)
            job.close();self.process,self.job=None,None
            atomic(self.state/'last-failure.json',dict(service=self.name,errorType=type(error).__name__,winerror=getattr(error,'winerror',None),at=time.time()))
            raise

    def capture(self):
        file = self.state/'logs'/(self.name+'.log');file.parent.mkdir(parents=True,exist_ok=True)
        secrets = [value for key,value in self.environment.items() if re.search(r'KEY|TOKEN|PASSWORD|SECRET|DATABASE_URL|AUTH|COOKIE|CREDENTIAL',key,re.I) and len(value)>3]
        database_file=self.environment.get('PGPASSWORD_FILE')
        if database_file:
            try:secrets.append(Path(database_file).read_text(encoding='utf-8').strip())
            except OSError:pass
        # DATABASE_URL may be logged as just its parsed password.
        from urllib.parse import urlsplit,unquote
        for value in list(secrets):
            try:
                password=urlsplit(value).password
                if password:secrets.append(unquote(password))
            except ValueError:pass
        try:
            with self.process.stdout as stream:
                while True:
                    raw=stream.readline(65537)
                    if not raw:break
                    if len(raw)>65536:
                        while raw and not raw.endswith(b'\n'):raw=stream.readline(65537)
                        continue
                    line=raw.decode('utf-8',errors='replace')
                    for secret in sorted(set(secrets),key=len,reverse=True):line=line.replace(secret,'[REDACTED]')
                    if file.exists() and file.stat().st_size > 2*1024*1024:
                        file.replace(file.with_suffix('.previous.log'))
                    with file.open('a',encoding='utf-8') as output:output.write(line)
        except (OSError,ValueError):
            pass

    def reap(self, now):
        if self.process is None or self.process.poll() is None:
            return False
        self.job.terminate();self.job.close();self.process.wait(timeout=10)
        if self.reader:self.reader.join(5)
        self.process,self.job=None,None
        self.next=now+self.delay
        return True

    def stop(self):
        if self.job:
            self.job.terminate()
            self.process.wait(timeout=10)
            self.job.close()
            if self.reader:self.reader.join(5)
            self.process,self.job=None,None


def allowed(config, preflight):
    if preflight:
        return False
    try:
        value=Path(config['executionAuthority']).read_text(encoding='utf-8').strip()
        return bool(config['authorityId']) and value == config['authorityId']
    except (OSError,KeyError):
        return False


def healthy(url):
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url,timeout=2) as response:
            data=json.load(response)
            return response.status == 200 and data.get('ok') is True and data.get('agent') is False
    except (OSError,ValueError):
        return False


def run(config, preflight=False):
    for key in ('sourceRoot','dataRoot','stateRoot','node','python','postgresBin','postgresData'):
        if not isinstance(config.get(key),str) or not Path(config[key]).is_absolute():
            raise ValueError('Required paths must be absolute')
    for key in ('cloudflared','executionAuthority','dnsSyncConfig'):
        if config.get(key) and not Path(config[key]).is_absolute():raise ValueError('Optional paths must be absolute')
    source,data,state=(Path(config[key]).resolve() for key in ('sourceRoot','dataRoot','stateRoot'))
    if not source.is_dir():raise ValueError('Source directory is missing')
    for value in (data,state):value.mkdir(parents=True,exist_ok=True)
    env={**os.environ,**config.get('environment',{})}
    if any(not isinstance(key,str) or not isinstance(value,str) for key,value in env.items()):
        raise ValueError('Environment must contain strings')
    if not env.get('DATABASE_URL','').startswith(('postgres://','postgresql://')) and not env.get('PGHOST'):
        raise ValueError('Independent production services require PostgreSQL')
    env.update(DATA_ROOT=str(data),EXTERNAL_AGENT='true',FISHBOT_MEMORY_POLICY='off',NODE_ENV='production')
    state.mkdir(parents=True,exist_ok=True)
    import msvcrt
    with (state/'supervisor.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        main_job=WindowsJob();main_job.assign()
        services=[]
        try:
            pg=Path(config['postgresBin']).resolve();pgdata=Path(config['postgresData']).resolve()
            node,python=str(Path(config['node']).resolve()),str(Path(config['python']).resolve())
            def service(name,command,owner=source,delay=10):
                item=Service(name,command,source,env,state,owner,delay);services.append(item);return item
            postgres=service('postgres',[str(pg/'postgres.exe'),'-D',str(pgdata),'-h','127.0.0.1','-p',env.get('PGPORT','5432')],pgdata,1)
            api=service('control-api',[node,'--max-old-space-size=128',str(source/'scripts/production-start.mjs')],delay=1)
            agent=service('agent',[node,'--max-old-space-size=128',str(source/'dist-server/apps/node-agent/src/main.js')])
            guard=service('memory-guard',[python,'-B',str(source/'scripts/windows-memory-guard.py'),'--state',str(state),'--health-url',f"http://127.0.0.1:{env.get('PORT','8787')}/v1/health"],delay=1)
            controller=service('memory-controller',[python,'-B',str(source/'scripts/windows-memory-controller.py'),'--root',str(data),'--state',str(state)])
            tunnel,dns_sync=None,None
            if config.get('cloudflared') and not preflight:
                tunnel=service('cloudflared',[str(Path(config['cloudflared']).resolve()),'tunnel','--url','http://127.0.0.1:8787','--no-autoupdate'])
                if config.get('dnsSyncConfig'):
                    dns_sync=service('dns-sync',[python,'-B',str(source/'scripts/tunnel-txt-sync.py'),'--config',str(Path(config['dnsSyncConfig']).resolve())])
            atomic(state/'supervisor.json',dict(pid=os.getpid(),created=psutil.Process().create_time(),preflight=preflight))
            last_health,api_ready=0,False
            while True:
                now=time.monotonic()
                stop=state/'supervisor.stop'
                try:
                    if int(stop.read_text().strip()) == os.getpid():
                        stop.unlink();break
                except (OSError,ValueError):pass
                for item in services:item.reap(now)
                if now-last_health >= 2:
                    api_ready=api.process is not None and healthy(f"http://127.0.0.1:{env.get('PORT','8787')}/v1/health")
                    last_health=now
                permitted=allowed(config,preflight)
                if dns_sync and dns_sync.process and not permitted:
                    dns_sync.stop();dns_sync.next=now+10
                if agent.process and not permitted:
                    agent.stop();agent.next=now+10
                if postgres.process is None and now>=postgres.next:postgres.start()
                pg_ready=False
                if postgres.process:
                    probe=subprocess.run([str(pg/'pg_isready.exe'),'-h','127.0.0.1','-p',env.get('PGPORT','5432')],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3,creationflags=0x08000000)
                    try:
                        pg_ready=probe.returncode==0 and postgres.process.poll() is None and int((pgdata/'postmaster.pid').read_text().splitlines()[0])==postgres.process.pid
                    except (OSError,ValueError,IndexError):pass
                if pg_ready and api.process is None and now>=api.next:api.start()
                if api_ready and permitted and agent.process is None and now>=agent.next:agent.start()
                # Preflight has no Agent record; guard correctly suspends memory
                # advice until deployment authorizes the full runtime contract.
                if api_ready:
                    if guard.process is None and now>=guard.next:guard.start()
                    if (state/'guard-heartbeat').exists() and controller.process is None and now>=controller.next:controller.start()
                    if tunnel and tunnel.process is None and now>=tunnel.next:tunnel.start()
                    if dns_sync and permitted and tunnel.process and tunnel.process.poll() is None and dns_sync.process is None and now>=dns_sync.next:dns_sync.start()
                time.sleep(.5)
        finally:
            # Keep Job protection until every managed tree has actually exited.
            pg_fast = None
            for item in reversed(services):
                if item.name=='postgres':
                    if item.process and item.process.poll() is None:
                        try:
                            result=subprocess.run([str(pg/'pg_ctl.exe'),'-D',str(pgdata),'-m','fast','-w','-t','15','stop'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,creationflags=0x08000000)
                            pg_fast=result.returncode==0
                        except (OSError,subprocess.SubprocessError):pg_fast=False
                item.stop()
            atomic(state/'supervisor-stopped.json',dict(pid=os.getpid(),created=psutil.Process().create_time(),at=time.time(),treesExited=True,postgresFastShutdown=pg_fast))
            main_job.kill_on_close(False);main_job.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--preflight',action='store_true',help='Never start Agent or tunnel, even with authority')
    args=parser.parse_args()
    try:
        run(json.loads(args.config.read_text(encoding='utf-8')),args.preflight)
    except Exception as error:
        # Never print config, command arguments, credentials or raw exceptions.
        print('Supervisor failed: '+type(error).__name__,flush=True)
        raise SystemExit(1)
