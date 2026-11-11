"""One media invocation: suspended native spawn, bounded owned Windows Job tree.

Parent stdin EOF revokes this invocation. Native stdout/stderr pass through;
credentials and arbitrary inherited service environment do not enter the tool.
"""
import ctypes
from ctypes import wintypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading

spec = importlib.util.spec_from_file_location('media_job_owner', Path(__file__).with_name('windows-production-supervisor.py'))
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class MediaJob(supervisor.WindowsJob):
    def configure(self):
        class Basic(ctypes.Structure):
            _fields_ = [('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),('flags',wintypes.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('activeLimit',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name,ctypes.c_ulonglong) for name in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
        class Limits(ctypes.Structure):
            _fields_ = [('basic',Basic),('io',Io),('processMemory',ctypes.c_size_t),('jobMemory',ctypes.c_size_t),('peakProcess',ctypes.c_size_t),('peakJob',ctypes.c_size_t)]
        limits = Limits()
        self.check(self.api.QueryInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits),None))
        limits.basic.flags |= 0x2000 | 0x8  # KILL_ON_JOB_CLOSE + ACTIVE_PROCESS.
        limits.basic.activeLimit = 64
        self.check(self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)))
        class Cpu(ctypes.Structure):
            _fields_ = [('flags',wintypes.DWORD),('rate',wintypes.DWORD)]
        # CpuRate is a fraction of the host's cycles (1/10000). A capped
        # ancestor can make this stricter. Process count is not a thread cap.
        # https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information
        cpu = Cpu(0x1 | 0x4, max(1, 2000 // (os.cpu_count() or 1)))
        self.check(self.api.SetInformationJobObject(self.handle,15,ctypes.byref(cpu),ctypes.sizeof(cpu)))


def run(command):
    if os.name != 'nt' or not command or not Path(command[0]).is_absolute():
        raise ValueError('Absolute Windows media executable required')
    canceled = threading.Event()
    def watch_parent():
        try:
            # Avoid a buffered-reader daemon lock during normal interpreter
            # shutdown while the still-live parent keeps this pipe open.
            os.read(sys.stdin.fileno(), 1)
        finally:
            canceled.set()
    threading.Thread(target=watch_parent, daemon=True).start()
    job = MediaJob()
    child = None
    assigned = False
    try:
        job.configure()  # Fail before executing native code if limits are denied.
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
            creationflags=0x00000004 | 0x00000200 | 0x08000000, close_fds=True)
        job.assign(child)
        assigned = True
        if not canceled.is_set():
            job.resume(child)
        while child.poll() is None and not canceled.wait(.025):
            pass
        return 1 if canceled.is_set() else child.returncode
    finally:
        try:
            if assigned:
                job.terminate(timeout=10)
                if job.active():
                    raise RuntimeError('Media Job did not drain')
            elif child is not None:
                child.kill()
            if child is not None:
                child.wait(timeout=2)
        finally:
            job.close()  # Never clear KILL_ON_JOB_CLOSE, including on exceptions.


if __name__ == '__main__':
    try:
        raise SystemExit(run(sys.argv[1:]))
    except Exception as error:
        print('Media Job failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
