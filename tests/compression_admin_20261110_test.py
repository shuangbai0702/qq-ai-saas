import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from compression_admin import manage


class CompressionAdmin(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[];self.gate=asyncio.Event();self.tracked=[]
        async def run():
            await self.gate.wait()
            return {'ok':True,'checkpointId':7}
        def start(scope,**kw):
            self.calls.append((scope,kw));self.reflection.task=asyncio.create_task(run())
            return {'started':True,'reason':'summary'}
        self.reflection=NS(force_compress_once=start,task=None)
        self.store=NS(shared_context=True,reflection=self.reflection)
        self.care=NS(busy=lambda st:bool(st.reflection.task and not st.reflection.task.done()),track=lambda st,task:self.tracked.append(task))
        self.data={'action':'admin_force_compress','requestId':'11111111-1111-4111-8111-111111111111','administrator':'admin-user','allowPartial':False}

    async def asyncTearDown(self):
        self.gate.set()
        if self.reflection.task:await self.reflection.task
        await asyncio.sleep(0)

    async def test_one_task_duplicate_and_completion_status(self):
        self.assertEqual(manage(self.store,self.data,self.care)['state'],'running')
        self.assertEqual(manage(self.store,self.data,self.care)['state'],'running')
        other={**self.data,'requestId':'22222222-2222-4222-8222-222222222222'}
        self.assertEqual(manage(self.store,other,self.care)['status'],409)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.tracked),1)
        self.assertFalse(self.calls[0][1]['allow_partial'])
        self.gate.set();await self.reflection.task;await asyncio.sleep(0)
        status=manage(self.store,{'action':'admin_force_compress_status','requestId':self.data['requestId']},self.care)
        self.assertEqual(status['state'],'completed');self.assertEqual(status['result']['checkpointId'],7)

    async def test_strict_command_and_known_runtime_only(self):
        for data in ({**self.data,'is_admin':True},{**self.data,'allowPartial':'true'},{**self.data,'administrator':''}):
            self.assertEqual(manage(self.store,data,self.care)['status'],400)
        self.store.shared_context=False
        self.assertEqual(manage(self.store,self.data,self.care)['status'],409)
        self.assertEqual(self.calls,[])

    async def test_cancelled_operation_is_reported(self):
        manage(self.store,self.data,self.care)
        self.reflection.task.cancel()
        with self.assertRaises(asyncio.CancelledError):await self.reflection.task
        await asyncio.sleep(0)
        self.assertEqual(manage(self.store,self.data,self.care)['state'],'cancelled')
        self.reflection.task=None

    async def test_failure_is_observed_without_exposing_error_content(self):
        async def fail():raise RuntimeError('private model details')
        def start(scope,**kwargs):
            self.reflection.task=asyncio.create_task(fail())
            return {'started':True,'reason':'summary'}
        self.reflection.force_compress_once=start
        manage(self.store,self.data,self.care)
        with self.assertRaises(RuntimeError):await self.reflection.task
        await asyncio.sleep(0)
        status=manage(self.store,self.data,self.care)
        self.assertEqual(status['state'],'failed');self.assertEqual(status['reason'],'RuntimeError')
        self.assertNotIn('private',str(status));self.reflection.task=None

    async def test_unconfirmed_completion_is_not_success(self):
        async def empty():return None
        def start(scope,**kwargs):
            self.reflection.task=asyncio.create_task(empty())
            return {'started':True,'reason':'summary'}
        self.reflection.force_compress_once=start
        manage(self.store,self.data,self.care);await self.reflection.task;await asyncio.sleep(0)
        status=manage(self.store,self.data,self.care)
        self.assertEqual(status['state'],'failed');self.assertEqual(status['reason'],'completion_unconfirmed')

if __name__=='__main__':unittest.main()
