"""Content-free process evidence; the existing owner still manages recovery."""
import asyncio
import faulthandler
import json
import os
import signal
import sys
import time


class StopFence:
    """An owner-controlled admission fence on the worker's event loop.

    Checking native activity and closing admission share a synchronous call.
    Ingress still persists to the existing queue while fenced; only the
    ProcessHost owner stops/replaces the process. No timer or task is added.
    """
    def __init__(self,activity):
        self.activity=activity
        self.quiescing=False

    def request_if_idle(self):
        if self.quiescing:return True
        status=self.activity()
        if not isinstance(status,dict) or status.get('idle') is not True:return False
        self.quiescing=True
        return True

    def cancel(self):
        self.quiescing=False


def record(event, **fields):
    print('FISHBOT_WORKER_LIFECYCLE '+json.dumps({'at':time.time(),'pid':os.getpid(),'event':event,**fields}),file=sys.stderr,flush=True)


def initialize():
    faulthandler.enable(all_threads=True)
    record('started')


def install_shutdown(stop):
    def received(sig):
        record('shutdown_signal',signal=signal.Signals(sig).name)
        stop.set()
    for sig in (signal.SIGINT,signal.SIGTERM):
        try:asyncio.get_running_loop().add_signal_handler(sig,received,sig)
        except (NotImplementedError,RuntimeError):pass


async def wait_for_shutdown(core_task, stop):
    waiter=asyncio.create_task(stop.wait())
    try:
        done,_=await asyncio.wait((core_task,waiter),return_when=asyncio.FIRST_COMPLETED)
        if core_task in done and not stop.is_set():
            record('core_stopped_unexpectedly')
            # Propagate the original failure; a living health server cannot
            # advertise a usable worker after its actual pipeline has died.
            await core_task
            raise RuntimeError('Worker core exited before shutdown')
    finally:
        waiter.cancel()
        await asyncio.gather(waiter,return_exceptions=True)
