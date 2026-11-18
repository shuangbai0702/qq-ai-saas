"""Start an independently owned protocol process using its one existing record."""
import argparse
import base64
import codecs
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time

import psutil

from process_control import owned_process, stop_tree


RECORD = 'protocol.process.json'
REQUEST = '.protocol-launch.json'
REQUEST_SECONDS = 12
LOG_LIMIT = 2 * 1024 * 1024
WINDOWS = os.name == 'nt'


class ProtocolError(Exception):
    pass


@contextmanager
def launch_lock(root):
    with (root / '.protocol-launch.lock').open('a+b') as file:
        if WINDOWS:
            import msvcrt

            deadline = time.monotonic() + 8
            while True:
                file.seek(0)
                try:
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ProtocolError('Timed out waiting for the protocol launch lock')
                    time.sleep(.05)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX)
        yield  # Closing the file releases the lock, including after process death.


def read_json(file):
    try:
        return json.loads(file.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


def atomic_json(file, value):
    # mkstemp uses mode 0600 on POSIX. On Windows the private Bot directory
    # supplies its ACL, and the credential payload also uses per-user DPAPI.
    fd, filename = tempfile.mkstemp(prefix=file.name + '.', dir=file.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
        os.replace(filename, file)
    finally:
        Path(filename).unlink(missing_ok=True)


def dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    operation = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    source = ctypes.create_string_buffer(data)
    original, result = Blob(len(data), ctypes.addressof(source)), Blob()
    if not operation(ctypes.byref(original), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def encode_request(config):
    if not WINDOWS:
        return config
    return base64.b64encode(dpapi(json.dumps(config).encode(), True)).decode('ascii')


def decode_request(data):
    return json.loads(dpapi(base64.b64decode(data), False)) if WINDOWS else data


def find_owner(root):
    record = read_json(root / RECORD)
    return record and owned_process(record['pid'], root, record.get('created'))


def authority_matches(authority):
    if authority is None:
        return True
    try:
        return Path(authority['file']).read_text(encoding='utf-8').strip() == authority['authorityId']
    except (OSError, UnicodeError):
        return False


def load_authority(root):
    if not WINDOWS or os.environ.get('EXTERNAL_AGENT') != 'true':
        return None
    authority = read_json(root.parent.parent / 'protocol-authority.json')
    if (
        not isinstance(authority, dict) or not isinstance(authority.get('file'), str)
        or not Path(authority['file']).is_absolute()
        or not isinstance(authority.get('authorityId'), str) or not authority['authorityId']
        or not authority_matches(authority)
    ):
        raise ProtocolError('Protocol execution authority is unavailable')
    return authority


def start_runner(root, token):
    args = [sys.executable, '-B', str(Path(__file__).resolve()), 'run', str(root), token]
    if not WINDOWS:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return
    # WMI creates the runner outside the calling Agent and Supervisor jobs.
    # The command contains only the private directory and a launch nonce.
    ps = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = {
        'command': subprocess.list2cmdline(args),
        'cwd': str(root),
        'flags': subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.DETACHED_PROCESS,
    }
    result = subprocess.run(
        [str(ps), '-NoProfile', '-NonInteractive', '-Command', """
        $ErrorActionPreference = 'Stop'
        $request = [Console]::ReadLine() | ConvertFrom-Json
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
            CreateFlags = [uint32]$request.flags
            ShowWindow = [uint16]0
        }
        $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = [string]$request.command
            CurrentDirectory = [string]$request.cwd
            ProcessStartupInformation = $startup
        }
        [Console]::WriteLine($result.ReturnValue)
        """],
        input=json.dumps(command) + '\n', text=True, encoding='ascii',
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=7,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise ProtocolError('WMI Win32_Process.Create is unavailable')
    status = int(result.stdout.strip())
    if status:
        raise ProtocolError(f'WMI Win32_Process.Create failed with status {status}')


def start(root, config):
    config['authority'] = load_authority(root)
    token = secrets.token_hex(16)
    ticket = root / REQUEST
    deadline = time.time() + REQUEST_SECONDS
    while time.time() < deadline:
        with launch_lock(root):
            if find_owner(root):
                return
            pending = read_json(ticket)
            if pending is None or pending['until'] <= time.time():
                atomic_json(ticket, dict(token=token, until=deadline, config=encode_request(config)))
                break
        time.sleep(.05)
    else:
        raise ProtocolError('Timed out waiting for an existing protocol launch')

    try:
        try:
            start_runner(root, token)
        except subprocess.TimeoutExpired:
            # WMI may have accepted the request before its caller timed out.
            # Only the owner record or the launch ticket can resolve it.
            pass
        while time.time() < deadline:
            with launch_lock(root):
                if find_owner(root):
                    return
                pending = read_json(ticket)
                if pending is None or pending['token'] != token:
                    raise ProtocolError('Protocol launch did not publish a process')
            time.sleep(.05)
        with launch_lock(root):
            if find_owner(root):
                return
        raise ProtocolError('Timed out waiting for the protocol process')
    finally:
        with launch_lock(root):
            pending = read_json(ticket)
            if pending and pending['token'] == token:
                ticket.unlink()


def stop(root, expected=None):
    if not root.exists():
        return
    with launch_lock(root):
        file = root / RECORD
        record = read_json(file)
        if expected is not None and (
            record is None or (record['pid'], record.get('created')) != expected
        ):
            raise ProtocolError('Process ownership changed before stop')
        (root / REQUEST).unlink(missing_ok=True)
        if record is None:
            return
        process = owned_process(record['pid'], root, record.get('created'))
        if process is None:
            try:
                created = psutil.Process(record['pid']).create_time()
            except psutil.NoSuchProcess:
                pass
            else:
                if record.get('created') is None or abs(created - record['created']) < .01:
                    raise ProtocolError('Protocol process identity did not match')
        if process is not None and not stop_tree(process):
            raise ProtocolError('Protocol process tree did not exit')
        file.unlink(missing_ok=True)


def own_process_tree(cgroup):
    if WINDOWS:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            'fishbot_windows_job', Path(__file__).with_name('windows-production-supervisor.py'),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        job = module.WindowsJob()
        job.assign()
        # The Job handle belongs only to this runner and is closed by the OS
        # when it exits, which also cleans any surviving protocol descendants.
        return job
    if cgroup:
        group = Path(cgroup['root']) / cgroup['id']
        group.mkdir(exist_ok=True)
        (group / 'memory.max').write_text(cgroup['memory'])
        (group / 'pids.max').write_text('128')
        (group / 'cpu.max').write_text(cgroup['cpu'])
        (group / 'cgroup.procs').write_text(str(os.getpid()))
    return None


def redact(text, keys):
    for key in keys:
        if len(key) > 2:
            text = text.replace(key, '[REDACTED]')
    text = re.sub(r'fish-platform\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}', '[REDACTED]', text, flags=re.I)
    text = re.sub(r'Bearer\s+[\w.\-]+', 'Bearer [REDACTED]', text, flags=re.I | re.ASCII)
    return re.sub(r'sk-[a-zA-Z0-9_-]{8,}', '[REDACTED]', text)


def write_log(root, line):
    file = root / 'runtime.log'
    try:
        if file.exists() and file.stat().st_size > LOG_LIMIT:
            os.replace(file, root / 'runtime.log.previous')
        with file.open('a', encoding='utf-8', newline='') as stream:
            stream.write('[protocol] ' + line)
    except OSError:
        # Logging may race an API read or another process's log rotation.
        pass


def read_stream(pipe, output, keys):
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    line, discarded = '', False
    for chunk in iter(lambda: pipe.read1(4096), b''):
        parts = decoder.decode(chunk).split('\n')
        for index, part in enumerate(parts):
            if not discarded:
                line += part
                if len(line) > 65536:
                    line, discarded = '', True
            if index < len(parts) - 1:
                if not discarded:
                    enqueue(output, redact(line, keys) + '\n')
                line, discarded = '', False
    tail = decoder.decode(b'', final=True)
    if not discarded and line + tail:
        enqueue(output, redact(line + tail, keys) + '\n')


def enqueue(output, text):
    try:
        output.put_nowait(text)
    except queue.Full:
        pass


def run(root, token):
    ticket = root / REQUEST
    file = root / RECORD
    with launch_lock(root):
        pending = read_json(ticket)
        if pending is None or pending['token'] != token:
            return
        try:
            if pending['until'] <= time.time() or find_owner(root):
                return
            config = decode_request(pending['config'])
            if not authority_matches(config['authority']):
                raise ProtocolError('Protocol execution authority expired before launch')
            # The Windows Job stays alive for the duration of the runner.
            job = own_process_tree(config.get('cgroup'))
            identity = psutil.Process()
            child = subprocess.Popen(
                [config['executable'], *config['args']],
                cwd=config['cwd'], env=config['env'], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0,
            )
            try:
                atomic_json(file, dict(pid=identity.pid, created=identity.create_time(),
                                       startedAt=datetime.now(timezone.utc).isoformat(), kind='protocol'))
            except BaseException:
                stop_tree(psutil.Process(child.pid))
                raise
        finally:
            ticket.unlink(missing_ok=True)

    write_log(root, f'process started at={datetime.now(timezone.utc).isoformat()} '
                    f'pid={identity.pid} source=ProcessHost.start\n')
    output = queue.Queue(maxsize=32)
    readers = [
        threading.Thread(target=read_stream, args=(pipe, output, config['keys']), daemon=True)
        for pipe in (child.stdout, child.stderr)
    ]
    for reader in readers:
        reader.start()
    authority_check, drain_until = time.monotonic(), None
    while True:
        now = time.monotonic()
        if child.poll() is not None:
            if drain_until is None:
                drain_until = now + 1
            if now >= drain_until or (output.empty() and not any(reader.is_alive() for reader in readers)):
                break
        if job is not None and now >= authority_check:
            if not authority_matches(config['authority']):
                write_log(root, 'execution authority revoked\n')
                return  # OS closure of the runner's Job also ends the protocol.
            authority_check = now + .5
        try:
            write_log(root, output.get(timeout=.1))
        except queue.Empty:
            pass
    write_log(root, f'process exited ({child.returncode}) at={datetime.now(timezone.utc).isoformat()} '
                    f'pid={identity.pid} intent=unexpected-or-external\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'stop', 'run'))
    parser.add_argument('root', type=Path)
    parser.add_argument('identity', nargs='*')
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.action == 'start':
            start(root, json.loads(sys.stdin.buffer.read().decode('utf-8')))
        elif args.action == 'stop':
            expected = None
            if args.identity:
                pid, created = args.identity
                expected = (int(pid), None if created == 'unknown' else float(created))
            stop(root, expected)
        else:
            run(root, args.identity[0])
    except ProtocolError as error:
        message = str(error)
    except Exception as error:
        # Never include the launch request, environment, or credentials in
        # error output. The exception type and system error identify failures.
        message = (f'Protocol {args.action} failed: {type(error).__name__} '
                   f'({getattr(error, "winerror", None) or getattr(error, "errno", None)})')
    else:
        return 0
    if args.action == 'run':
        write_log(root, message + '\n')
    else:
        print(message, file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
