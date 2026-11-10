"""Deterministic characterization of the native idle check / stop boundary."""
import ast
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/astrbot'))
from runtime_care import RuntimeCare


def store():
    return SimpleNamespace(active=0, ingress=0, waiting=0, batches={},
                           social=SimpleNamespace(task=None, stickers=SimpleNamespace(task=None)),
                           reflection=SimpleNamespace(task=None), planner=SimpleNamespace(task=None))


class ManagedStop(unittest.IsolatedAsyncioTestCase):
    async def test_native_pipeline_await_is_not_idle_even_without_model_activity(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def pipeline():
            entered.set()
            await release.wait()
        pending = asyncio.create_task(pipeline())
        await entered.wait()
        core = SimpleNamespace(event_queue=asyncio.Queue(), event_bus=SimpleNamespace(_pending_tasks={pending: object()}))
        care = RuntimeCare({'member': store()})
        try:
            self.assertEqual(care.status(core)['pending'], 1)
            self.assertFalse(care.status(core)['idle'])
            release.set()
            await pending
            self.assertTrue(care.status(core)['idle'])
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_admitted_work_remains_busy_until_ingress_finally_settles(self):
        member = store()
        member.ingress = 1
        care = RuntimeCare({'member': member})
        core = SimpleNamespace(event_queue=asyncio.Queue(), event_bus=SimpleNamespace(_pending_tasks={}))
        self.assertFalse(care.status(core)['idle'])
        member.ingress = 0
        self.assertTrue(care.status(core)['idle'])

    async def test_current_owner_stop_can_cancel_work_admitted_after_idle_sample(self):
        # Execute the real shared-worker owner cleanup body without booting QQ.
        # This characterizes the existing unsafe inter-process check/stop gap;
        # it does not authorize replaying a cancelled root or claim a drain fix.
        tree = ast.parse((ROOT / 'adapters/astrbot/shared-worker.py').read_text(encoding='utf8'))
        main = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'main')
        shutdown = next(n for n in main.body if isinstance(n, ast.Try) and n.finalbody)
        fn = ast.AsyncFunctionDef(name='owner_cleanup', args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=n) for n in ('core','task','runner','behavior')], kwonlyargs=[], kw_defaults=[], defaults=[]), body=shutdown.finalbody, decorator_list=[])
        code = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        namespace = {}
        exec(compile(code, 'shared-worker-owner-cleanup', 'exec'), namespace)
        pending = {}
        queue = asyncio.Queue()
        cancelled = asyncio.Event()
        entered = asyncio.Event()
        closed = []
        class Core:
            event_queue = queue
            event_bus = SimpleNamespace(_pending_tasks=pending)
            async def stop(self):
                # SDK teardown cancels native pipeline owners.
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        core = Core()
        care = RuntimeCare({'member': store()})
        self.assertTrue(care.status(core)['idle'])
        async def pipeline():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        work = asyncio.create_task(pipeline())
        pending[work] = object()
        await entered.wait()
        self.assertFalse(care.status(core)['idle'])
        core_task = asyncio.create_task(asyncio.Event().wait())
        async def cleanup():
            closed.append('http')
        await namespace['owner_cleanup'](core, core_task, SimpleNamespace(cleanup=cleanup), {'close': lambda: closed.append('behavior')})
        await asyncio.gather(core_task, return_exceptions=True)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(closed, ['http', 'behavior'])


if __name__ == '__main__':
    unittest.main()
