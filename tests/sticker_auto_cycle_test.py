"""Sticker auto-cycle: caption auto approve/reject via CAS, bounded pending backfill, manual review wins."""
import asyncio,base64,io,json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'adapters/astrbot'))
import sticker_collection
from sticker_collection import StickerCollector,manage
from PIL import Image

SCOPE='group:12345'
def png_bytes(color):
    out=io.BytesIO();Image.new('RGB',(32,32),color).save(out,format='PNG');return out.getvalue()

def raw_message(event,url='https://gchat.qpic.cn/fixture'):
    return {'message_id':event,'message':[{'type':'mface','data':{'url':url}}]}

class FakeProvider:
    def __init__(self,*results):self.results=list(results);self.calls=[]
    async def text_chat(self,**kw):
        self.calls.append(kw)
        if not self.results:raise AssertionError('unexpected extra model call')
        result=self.results.pop(0)
        if isinstance(result,Exception):raise result
        return NS(completion_text=result)

class BlockingProvider:
    def __init__(self,gate,result):self.gate=gate;self.result=result;self.calls=[]
    async def text_chat(self,**kw):
        self.calls.append(kw);await self.gate.wait();return NS(completion_text=self.result)

class Cycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=sqlite3.connect(':memory:');self.collector=StickerCollector(self.db);self.metrics={}
    async def asyncTearDown(self):
        self.collector.close();self.db.close()
    def seed(self,identifier,created,status='pending',version=1,png=b'seed-bytes',scope=SCOPE,meaning=''):
        self.db.execute('INSERT INTO sticker_inbox VALUES(?,?,?,?,?,?,?,?,?)',(identifier,scope,'subject','event',status,meaning,created,version,png));self.db.commit()
    async def observe_one(self,provider,raw=None,event='e1'):
        raw=png_bytes((120,40,200)) if raw is None else raw
        async def fetch(url,max_bytes=1048576):return raw
        cfg={'stickerCollect':True,'stickerAllGroups':True}
        self.collector.observe(raw_message(event),SCOPE,'sender','bot',cfg,lambda:True,self.metrics,fetch=fetch,provider=provider)
        if self.collector.task:await self.collector.task

    async def test_auto_approve_makes_collected_sticker_usable(self):
        provider=FakeProvider(json.dumps({'meaning':'开心大笑','suitable':True}))
        await self.observe_one(provider)
        status,meaning,version,png=self.db.execute('SELECT status,meaning,version,png FROM sticker_inbox').fetchone()
        self.assertEqual((status,meaning,version),('approved','开心大笑',2))
        self.assertIsNotNone(png);self.assertEqual(self.db.execute('SELECT count(*) FROM sticker_originals').fetchone()[0],1)
        self.assertEqual(self.metrics.get('stickerAutoApproved'),1);self.assertEqual(self.metrics.get('stickerCaptioned'),1)
        self.assertEqual(self.db.execute('SELECT count FROM sticker_caption_budget').fetchone()[0],1)
        items=self.collector.catalog(SCOPE)
        self.assertEqual(len(items),1);self.assertEqual(items[0]['meaning'],'开心大笑');self.assertTrue(items[0]['sticker'])
        image=self.collector.image(items[0]['id'],SCOPE)
        self.assertTrue(image.startswith(b'\x89PNG'))
        embedded=provider.calls[0]['image_urls'][0]
        self.assertTrue(embedded.startswith('data:image/png;base64,'))
        self.assertEqual(base64.b64decode(embedded.split(',',1)[1]),png)

    async def test_auto_reject_frees_pending_slot_for_new_collection(self):
        provider=FakeProvider(json.dumps({'meaning':'无关内容','suitable':False}))
        await self.observe_one(provider,event='e1')
        status,png=self.db.execute('SELECT status,png FROM sticker_inbox').fetchone()
        self.assertEqual(status,'rejected');self.assertIsNone(png)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sticker_originals').fetchone()[0],0)
        self.assertEqual(self.metrics.get('stickerAutoRejected'),1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sticker_inbox WHERE status='pending'").fetchone()[0],0)
        self.db.execute('DELETE FROM sticker_attempts');self.db.commit()
        await self.observe_one(FakeProvider(json.dumps({'meaning':'新的开心','suitable':True})),raw=png_bytes((10,200,30)),event='e2')
        self.assertEqual(self.metrics.get('stickerCollected'),2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sticker_inbox WHERE status='approved'").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sticker_inbox WHERE status='rejected'").fetchone()[0],1)

    async def test_backfill_bounded_by_three_and_daily_budget(self):
        for i in range(5):self.seed('cap_%02d'%i,created=1000.0+i)
        self.db.execute("INSERT INTO sticker_caption_budget VALUES('2026-01-01',1)");self.db.commit()
        provider=FakeProvider(*[json.dumps({'meaning':'历史情绪%d'%i,'suitable':True}) for i in range(5)])
        self.collector.maybe_backfill(provider,lambda:True,self.metrics)
        self.assertIsNotNone(self.collector.backfill_task)
        await self.collector.backfill_task
        self.assertEqual(len(provider.calls),3)
        self.assertEqual({r[0] for r in self.db.execute("SELECT id FROM sticker_inbox WHERE status='approved'")},{'cap_00','cap_01','cap_02'})
        self.assertEqual(self.db.execute("SELECT count(*) FROM sticker_inbox WHERE status='pending'").fetchone()[0],2)
        self.assertEqual(self.metrics.get('stickerCaptioned'),3);self.assertEqual(self.metrics.get('stickerAutoApproved'),3)
        rows=self.db.execute('SELECT day,count FROM sticker_caption_budget').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0][1],3)
        first=self.collector.backfill_task
        self.collector.maybe_backfill(provider,lambda:True,self.metrics)
        self.assertIs(self.collector.backfill_task,first)

    async def test_backfill_skips_byteless_once_and_is_single_flight(self):
        self.seed('cap_byteless',created=1000.0,png=None)
        self.seed('cap_ok',created=1001.0)
        gate=asyncio.Event();provider=BlockingProvider(gate,json.dumps({'meaning':'补充情绪','suitable':True}))
        self.collector.maybe_backfill(provider,lambda:True,self.metrics)
        first=self.collector.backfill_task;await asyncio.sleep(0)
        self.collector.maybe_backfill(provider,lambda:True,self.metrics)
        self.assertIs(self.collector.backfill_task,first)
        gate.set();await first
        self.assertEqual(len(provider.calls),1)
        self.assertEqual(self.metrics.get('stickerBackfillSkipped'),1)
        self.assertEqual(self.db.execute("SELECT status FROM sticker_inbox WHERE id='cap_byteless'").fetchone()[0],'pending')
        self.assertEqual(self.db.execute("SELECT status FROM sticker_inbox WHERE id='cap_ok'").fetchone()[0],'approved')
        self.assertEqual(self.db.execute('SELECT count FROM sticker_caption_budget').fetchone()[0],1)

    async def test_manual_review_version_blocks_auto_flow(self):
        self.seed('cap_manual_approve',created=1000.0)
        self.seed('cap_manual_reject',created=1001.0)
        self.seed('cap_auto1',created=1002.0)
        self.seed('cap_auto2',created=1003.0)
        self.assertEqual(manage(self.db,{'action':'review_sticker','id':'cap_manual_approve','status':'approved','meaning':'人工保留','expectedVersion':1}),{'ok':True})
        self.assertEqual(manage(self.db,{'action':'review_sticker','id':'cap_manual_reject','status':'rejected','expectedVersion':1}),{'ok':True})
        provider=FakeProvider(json.dumps({'meaning':'自动语义','suitable':True}),json.dumps({'meaning':'不要','suitable':False}))
        self.collector.maybe_backfill(provider,lambda:True,self.metrics);await self.collector.backfill_task
        self.assertEqual(self.db.execute("SELECT status,meaning,version FROM sticker_inbox WHERE id='cap_manual_approve'").fetchone(),('approved','人工保留',2))
        self.assertEqual(self.db.execute("SELECT status,version FROM sticker_inbox WHERE id='cap_manual_reject'").fetchone(),('rejected',2))
        self.assertEqual(self.db.execute("SELECT status FROM sticker_inbox WHERE id='cap_auto1'").fetchone()[0],'approved')
        self.assertEqual(self.db.execute("SELECT status FROM sticker_inbox WHERE id='cap_auto2'").fetchone()[0],'rejected')
        self.assertEqual(len(provider.calls),2)

    async def test_auto_approve_refused_at_twenty_cap(self):
        for i in range(20):self.seed('cap_full_%02d'%i,created=500.0+i,status='approved',version=2,meaning='已有')
        self.seed('cap_wait',created=2000.0)
        provider=FakeProvider(json.dumps({'meaning':'超出上限','suitable':True}))
        self.collector.maybe_backfill(provider,lambda:True,self.metrics);await self.collector.backfill_task
        self.assertEqual(self.db.execute("SELECT status,version,meaning FROM sticker_inbox WHERE id='cap_wait'").fetchone(),('pending',1,''))
        self.assertEqual(self.db.execute("SELECT count(*) FROM sticker_inbox WHERE status='approved'").fetchone()[0],20)
        self.assertEqual(len(provider.calls),1)
        self.assertNotIn('stickerAutoApproved',self.metrics);self.assertNotIn('stickerCaptioned',self.metrics)
        self.assertEqual(self.db.execute('SELECT count FROM sticker_caption_budget').fetchone()[0],1)
        self.db.execute('DELETE FROM sticker_attempts');self.db.commit()
        await self.observe_one(FakeProvider(json.dumps({'meaning':'还是超限','suitable':True})),event='cap-observe')
        self.assertEqual(self.db.execute("SELECT status,version FROM sticker_inbox WHERE status='pending'").fetchall(),[('pending',1),('pending',1)])
        self.assertEqual(self.db.execute("SELECT count(*) FROM sticker_inbox WHERE status='approved'").fetchone()[0],20)

    async def test_invalid_and_boundary_captions_keep_pending(self):
        self.seed('cap_broken',created=1000.0)
        self.seed('cap_short',created=1001.0)
        self.seed('cap_long',created=1002.0)
        provider=FakeProvider('not-json',json.dumps({'meaning':'短','suitable':True}),json.dumps({'meaning':'长'*161,'suitable':True}))
        self.collector.maybe_backfill(provider,lambda:True,self.metrics);await self.collector.backfill_task
        self.assertEqual(len(provider.calls),3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sticker_inbox WHERE status='pending' AND version=1").fetchone()[0],3)
        self.assertEqual(self.metrics.get('stickerCaptionFailures'),1)
        self.assertNotIn('stickerAutoApproved',self.metrics)
        self.assertEqual(self.db.execute('SELECT count FROM sticker_caption_budget').fetchone()[0],3)

if __name__=='__main__':unittest.main(verbosity=2)
