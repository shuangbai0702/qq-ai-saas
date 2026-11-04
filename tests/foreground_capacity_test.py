import asyncio,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
from behavior import BehaviorStore

class Capacity(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.store=object.__new__(BehaviorStore)
  self.store.active=2;self.store.waiting=0;self.store.slot_changed=asyncio.Event()
 async def test_released_slot_wakes_an_admitted_request(self):
  task=asyncio.create_task(self.store.wait_capacity(lambda:True))
  await asyncio.sleep(0);self.assertEqual(self.store.waiting,1)
  self.store.active=1;self.store.slot_changed.set()
  self.assertTrue(await task);self.assertEqual(self.store.waiting,0)
 async def test_cancellation_releases_waiter_and_stale_work_never_enters(self):
  task=asyncio.create_task(self.store.wait_capacity(lambda:True));await asyncio.sleep(0)
  task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(self.store.waiting,0)
  valid=True;task=asyncio.create_task(self.store.wait_capacity(lambda:valid));await asyncio.sleep(0)
  valid=False;self.store.active=1;self.store.slot_changed.set()
  self.assertFalse(await task);self.assertEqual(self.store.waiting,0)
 async def test_queue_and_wait_are_bounded_without_spawning_model_work(self):
  self.store.waiting=8;self.assertFalse(await self.store.wait_capacity(lambda:True));self.assertEqual(self.store.waiting,8)
  self.store.waiting=0;self.assertFalse(await self.store.wait_capacity(lambda:True,timeout=0));self.assertEqual(self.store.waiting,0)

if __name__=='__main__':unittest.main()
