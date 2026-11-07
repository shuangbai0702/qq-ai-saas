"""Legacy tool Hold recovery uses actual evidence and the original 35-round license."""
import asyncio,contextvars,copy,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot')]
from behavior import BehaviorStore
import summary_material as material
from context_protocol import SOURCE,model_json
SCOPE='private:12345678'
def user(event):return {'role':'user','content':SOURCE+model_json({'scope':SCOPE,'sender_id':'12345678','event_id':str(event)})+'\n'+'真实请求'+str(event)}
def block(event):return [user(event),{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'propose_background_task','arguments':'{"task":"review"}'}}]},{'role':'tool','tool_call_id':'call1','content':'{"accepted":true,"taskId":"one"}'},{'role':'assistant','content':'原始模型回复'}]
class LegacyToolHoldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.db=self.store.db;self.calls=[]
    def tearDown(self):self.store.close();self.tmp.cleanup()
    async def drain(self):
        for _ in range(5):
            task=self.store.reflection.task
            if task:await task
            await asyncio.sleep(0)
            if self.store.reflection.task is task:return
    async def test_existing_checkpoint_promotes_without_new_rounds_or_tool_replay(self):
        for i in range(35):
            messages=block(i) if i==0 else [user(i),{'role':'assistant','content':'已确认回复'}]
            items=material.snapshots(messages,SCOPE)
            if i==0:items[0].update(version=1,complete=False);items[0].pop('tools')
            self.store.reflection.record(SCOPE,'12345678',str(i),'真实请求'+str(i),'已确认回复',time.time()-1000+i,materials=items)
        async def chat(**kw):self.calls.append(json.loads(kw['prompt']));return NS(completion_text='{"summary":"保留本批真实请求和工具结果，未完成工作以任务账本为准。"}')
        ref=self.store.reflection;metrics={};ref.binding=({'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=chat),lambda:True,metrics,contextvars.ContextVar('test',default=None))
        self.assertTrue(ref.retry());await self.drain();self.assertEqual(len(self.calls),1)
        watermark=self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?',(SCOPE,)).fetchone()[0]
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'pending_coverage')
        def recapture(scope,events):material.remember(self.db,scope,material.snapshots(block(0),scope))
        ref.recapture=recapture;self.assertTrue(ref.retry());await self.drain()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchall(),[('available',)])
        self.assertEqual(self.db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?',(SCOPE,)).fetchone()[0],watermark)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],0)
        observed=next(x for x in self.calls[-1]['observed_requests'] if x.get('tools'))
        self.assertEqual(observed['confirmed_reply'],'已确认回复');self.assertEqual(observed['native_reply'],'原始模型回复')
        self.assertEqual(observed['tools'][0]['arguments'],'{"task":"review"}')
    def test_tool_coverage_requires_unchanged_receipt_and_complete_pair(self):
        messages=block(1);item=material.snapshots(messages,SCOPE)[0];self.assertTrue(item['complete']);self.assertTrue(material.valid_material(item,SCOPE))
        proof={'1':{'subject':'12345678','digest':item['digest'],'reply_digest':material.reply_digest(messages),'tools_digest':material.tools_digest(item['tools'])}}
        self.assertEqual(material.project_history(messages+[user(2)],proof,SCOPE,'2'),[user(2)])
        tampered=copy.deepcopy(messages);tampered[2]['content']='different receipt'
        self.assertEqual(material.project_history(tampered+[user(2)],proof,SCOPE,'2'),tampered+[user(2)])
        missing=messages[:2]+messages[3:];self.assertFalse(material.snapshots(missing,SCOPE)[0]['complete'])
    def test_protected_or_missing_evidence_does_not_trigger_paid_recovery(self):
        ref=self.store.reflection
        self.db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,?,?,?)',(SCOPE,'1',1,'protected',time.time()))
        called=[];ref.recapture=lambda *args:called.append(args)
        self.assertFalse(ref.hold_recovery_ready(SCOPE,[(1,'12345678','1','q','a',1)],time.time(),{}));self.assertEqual(called,[])
        self.db.execute("UPDATE memory_hold_rounds SET reason='uncovered'")
        self.assertFalse(ref.hold_recovery_ready(SCOPE,[(1,'12345678','1','q','a',1)],time.time(),{}));self.assertEqual(len(called),1)
        self.assertFalse(ref.hold_recovery_ready(SCOPE,[(1,'12345678','1','q','a',1)],time.time(),{}));self.assertEqual(len(called),1)
if __name__=='__main__':unittest.main()
