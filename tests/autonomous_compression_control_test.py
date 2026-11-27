"""Control IPC stays responsive while the original compression owns cognition."""
import asyncio,copy,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
from autonomous_runtime import AutonomousRuntime
from behavior import BehaviorStore


class CompressionControl(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.st=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
        self.settings={'qq':{'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'config':{}}}
        self.runtime=AutonomousRuntime(NS(event_bus=NS(stopped_platforms=set())),self.settings,lambda *_:self.st,lambda *_:None,{},None,None)
        self.policy={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'epoch':1,'issuedAt':1,'until':time.time()*1000+14000,'config':{'mode':'active','rollout':'live'}}
    def tearDown(self):self.runtime.close();self.st.close();self.tmp.cleanup()

    async def test_policy_snapshot_and_cancel_do_not_wait_or_touch_retiring_sqlite(self):
        self.st.compression_active=True;self.st.compression_boundary=0
        original=self.st.db
        class NoSql:
            def execute(self,*_):raise AssertionError('control IPC touched retiring data')
        self.st.db=NoSql()
        try:
            result=await asyncio.wait_for(self.runtime.handle('qq','policy',self.policy),.5)
            self.assertFalse(result['pendingHandoffs'])
            self.assertEqual(await asyncio.wait_for(self.runtime.handle('qq','snapshot',{}),.5),{'foregroundBusy':True,'compressionBusy':True,'feedbackVersion':0})
            self.assertEqual(await asyncio.wait_for(self.runtime.handle('qq','agreement_tasks',{'epoch':1,'states':[]}),.5),{'applied':False,'reason':'compression_busy'})
            self.assertEqual(await asyncio.wait_for(self.runtime.handle('qq','cancel',{}),.5),{'ok':True})
            self.assertFalse(self.runtime.tasks)
        finally:self.st.db=original;self.st.compression_active=False;self.st.compression_boundary=None

    async def test_execution_keeps_compression_fence_and_rejects_replaced_member(self):
        self.st.compression_active=True;self.st.compression_boundary=0
        task=asyncio.create_task(self.runtime.handle('qq','execute',{}));await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.settings['qq']={**self.settings['qq'],'generation':2}
        self.st.compression_active=False;self.st.compression_boundary=None;self.st.slot_changed.set()
        with self.assertRaisesRegex(ValueError,'stale_internal_work'):await task
        self.assertFalse(self.runtime.tasks)

    async def test_stale_policy_or_task_sync_cannot_add_terminal_evidence(self):
        import agreement_state
        self.runtime.install('qq',self.policy)
        stale={**copy.deepcopy(self.policy),'issuedAt':0}
        from unittest.mock import patch
        with patch.object(agreement_state,'sync_task_terminals',side_effect=AssertionError('stale policy wrote evidence')):
            with self.assertRaisesRegex(ValueError,'stale_autonomy_permit'):self.runtime.install('qq',stale)
            with self.assertRaisesRegex(ValueError,'stale_agreement_tasks'):await self.runtime.handle('qq','agreement_tasks',{'epoch':2,'states':[]})
            self.settings['qq']={**self.settings['qq'],'generation':2}
            with self.assertRaisesRegex(ValueError,'stale_agreement_tasks'):await self.runtime.handle('qq','agreement_tasks',{'epoch':1,'states':[]})


if __name__=='__main__':unittest.main()
