"""Deterministic event-loop admission ownership, no production processes."""
import asyncio,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from worker_lifecycle import StopFence


class FenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_and_unknown_activity_cannot_fence(self):
        for state in ({'idle':False,'pending':1},{},None):
            fence=StopFence(lambda:state)
            self.assertFalse(fence.request_if_idle())
            self.assertFalse(fence.quiescing)

    async def test_admission_and_idle_decision_have_no_await_gap(self):
        pending=0;persisted=[];executed=[]
        fence=StopFence(lambda:{'idle':pending==0})
        async def incoming():
            # Matches the worker gate: persistence precedes the fence check.
            persisted.append('real-input')
            if not fence.quiescing:executed.append('real-input')
        task=asyncio.create_task(incoming())
        self.assertTrue(fence.request_if_idle())
        await task
        self.assertEqual(persisted,['real-input'])
        self.assertEqual(executed,[])
        self.assertTrue(fence.request_if_idle())
        fence.cancel()
        await incoming()
        self.assertEqual(executed,['real-input'])

    async def test_an_admitted_pipeline_wins_before_stop_request(self):
        pending=0
        fence=StopFence(lambda:{'idle':pending==0})
        pending=1
        self.assertFalse(fence.request_if_idle())
        pending=0
        self.assertTrue(fence.request_if_idle())


if __name__=='__main__':unittest.main()
