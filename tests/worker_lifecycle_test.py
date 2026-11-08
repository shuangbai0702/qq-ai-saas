import asyncio,contextlib,io,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import worker_lifecycle as lifecycle


class Worker(unittest.IsolatedAsyncioTestCase):
    async def test_core_failure_is_observed_without_waiting_for_signal(self):
        async def failed():raise ValueError('controlled failure')
        stream=io.StringIO()
        with contextlib.redirect_stderr(stream):
            with self.assertRaisesRegex(ValueError,'controlled failure'):
                await lifecycle.wait_for_shutdown(asyncio.create_task(failed()),asyncio.Event())
        evidence=json.loads(stream.getvalue().split(' ',1)[1])
        self.assertEqual(evidence['event'],'core_stopped_unexpectedly')
    async def test_clean_core_exit_is_not_a_healthy_idle_worker(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'before shutdown'):
                await lifecycle.wait_for_shutdown(asyncio.create_task(asyncio.sleep(0)),asyncio.Event())
    async def test_shutdown_does_not_cancel_core_before_owner_cleanup(self):
        stop=asyncio.Event();stop.set();core=asyncio.create_task(asyncio.Event().wait())
        await lifecycle.wait_for_shutdown(core,stop)
        self.assertFalse(core.done());core.cancel();await asyncio.gather(core,return_exceptions=True)
    async def test_cancellation_cleans_only_its_own_waiter(self):
        core=asyncio.create_task(asyncio.Event().wait());stop=asyncio.Event()
        task=asyncio.create_task(lifecycle.wait_for_shutdown(core,stop));await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(core.done());core.cancel();await asyncio.gather(core,return_exceptions=True)


if __name__=='__main__':unittest.main()
