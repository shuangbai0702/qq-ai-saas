import asyncio,json,os,sqlite3,sys,unittest,contextvars,time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'),str(Path(__file__).resolve().parents[1]/'references'/'astrbot')]
from memory_reflection import Reflection,invalidate
from hosted_memory import ScopedMemory
from data_admin import run

from summary_stream_fixture import summary_provider

class LongMemory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.r=Reflection(self.db);self.calls=[]
        self.db.execute('CREATE TABLE facts(scope TEXT,subject TEXT,name TEXT,value TEXT,event TEXT,updated REAL,version INTEGER,active INTEGER)')
        self.facts=ScopedMemory(self.db)
        self.cfg={'memorySemantic':True,'embeddingModel':'bge-m3','dailyEmbeddingLimit':1000}
        async def create(**kw):
            self.calls.append(kw)
            return NS(data=[NS(index=i,embedding=[1.,0.] if t in ('drink','tea') else [0.,1.]) for i,t in enumerate(kw['input'])])
        self.client=NS(base_url='https://chat.example/v1',api_key='test-bot-key',embeddings=NS(create=create))
        self.client.with_options=lambda **kw:self.client
        self.provider=NS(client=self.client)
    def tearDown(self):self.r.close();self.db.close()
    def summary(self,text,scope='group:1'):
        self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,text,'["real-event"]',1));self.db.commit()
    async def test_person_correction_preserves_other_people_raw_evidence(self):
        for scope,person,event in [('group:12345','12345678','a'),('group:12345','23456789','b'),('private:12345678','12345678','c')]:
            self.r.record(scope,person,event,'真实输入','真实回答',1)
        invalidate(self.db,'group:12345','12345678');self.db.commit()
        self.assertEqual(self.db.execute('SELECT subject,event FROM memory_pending').fetchall(),[('23456789','b')])
        self.assertEqual(self.db.execute('SELECT subject,event FROM memory_rounds').fetchall(),[('23456789','b')])
        self.assertEqual(self.r.epoch(),1)
    def recall(self):
        return self.r.foundation('group:1')
    async def test_fixed_basis_is_bounded_without_indexing_library(self):
        self.summary('tea');self.summary('private secret','group:2')
        for i in range(20):self.summary('unrelated '+str(i))
        result=self.recall()
        self.assertEqual([item['summary'] for item in result],['tea','unrelated 0'])
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='memory_vectors'").fetchone())
        self.assertFalse(self.calls)
    async def test_summary_reader_never_constructs_independent_embedding_client(self):
        import model_egress
        self.summary('tea');self.cfg['embeddingBaseUrl']='https://embedding.example/v1'
        with patch.object(model_egress,'client',side_effect=AssertionError('unexpected embedding client')):
            self.assertTrue(self.recall())
        self.assertFalse(self.calls)
    async def test_summary_read_has_no_awaited_embedding_resource(self):
        self.summary('tea')
        async def unsupported(**kw):raise AssertionError('unexpected embedding')
        self.client.embeddings.create=unsupported
        self.assertTrue(self.recall())
        self.assertFalse(self.r.embedding_lock.locked())
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='memory_vectors'").fetchone())
    async def test_real_private_origin_policy(self):
        import model_egress,httpx
        backend=NS(connect_tcp=None)
        async def connect(*args,**kwargs):return 'connected'
        backend.connect_tcp=connect
        loop=asyncio.get_running_loop()
        async def resolve(*args,**kwargs):return [(2,1,6,'',('127.0.0.1',11434))]
        with patch.object(loop,'getaddrinfo',side_effect=resolve):
            with self.assertRaises(httpx.ConnectError):await model_egress.GuardedBackend(backend).connect_tcp('localhost',11434)
            self.assertEqual(await model_egress.GuardedBackend(backend,('localhost',11434)).connect_tcp('localhost',11434),'connected')
        with patch.dict(os.environ,{'FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS':'http://localhost:11434'}):
            client=model_egress.client({'api_base':'http://localhost:11434/v1'})
            self.assertEqual(client._transport._pool._network_backend.trusted,('localhost',11434));await client.aclose()
    async def test_current_and_closed_summary_reader_fences(self):
        self.summary('tea')
        invalidate(self.db,'group:1');self.db.commit()
        self.assertEqual(self.recall(),[])
        self.summary('new evidence');self.r.close()
        self.assertEqual(self.recall(),[])
        self.assertFalse(self.calls)
    async def test_late_reflection_cannot_restore_deleted_sources(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def text_chat(**kw):entered.set();await release.wait();return NS(completion_text=json.dumps({'summary':'真实话题摘要'}))
        for i in range(30):self.r.record('group:1','123',str(i),'question','answer')
        self.r.maybe_start('group:1',{'memorySummary':True,'summaryRounds':30},summary_provider(text_chat),lambda:True,{},contextvars.ContextVar('event',default=None))
        await asyncio.wait_for(entered.wait(),3);self.facts.observe('group:1','123','更正记忆：称呼=新名字','correct',2);self.db.commit()
        release.set();await self.r.task
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
    async def test_forget_blocks_automatic_recapture(self):
        self.facts.observe('group:1','123','我喜欢喝红茶','1',1,True)
        self.facts.observe('group:1','123','忘记：饮料偏好','2',2,True)
        status=self.facts.remember('group:1','123','饮料偏好','红茶','3',3,origin='self_report')
        self.assertIn('不会自动重新保存',status)
        self.assertFalse(self.facts.recall('group:1','123','饮料',4))

class ReflectionDebt(unittest.IsolatedAsyncioTestCase):
    async def test_busy_group_cannot_evict_another_conversation_before_summary(self):
        db=sqlite3.connect(':memory:');r=Reflection(db);now=time.time()
        quiet='private:12345678';busy='group:55555'
        try:
            for n in range(30):r.record(quiet,'12345678','quiet'+str(n),'安静会话的问题','已确认回答',now-100+n)
            for n in range(996):
                db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(busy,'87654321','busy'+str(n),'群聊消息','已确认回答',now+n/1000))
                db.execute('INSERT INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)',(busy,'87654321','busy'+str(n),'群聊消息','已确认回答',now+n/1000))
            db.commit();r.record(busy,'87654321','new-busy','新的群聊消息','已确认回答',now+1)
            for table in ('memory_pending','memory_rounds'):
                self.assertGreaterEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],1000)
                self.assertEqual(db.execute('SELECT count(*) FROM '+table+' WHERE scope=?',(quiet,)).fetchone()[0],30)
            calls=[]
            async def chat(**kw):calls.append(kw);return NS(completion_text=json.dumps({'summary':'安静会话的真实摘要'}))
            r.maybe_start(busy,{'memorySummary':True,'summaryRounds':30,'naturalConversation':False},summary_provider(chat),lambda:True,{},contextvars.ContextVar('fair_debt',default=None));await r.task
            self.assertEqual(db.execute('SELECT scope FROM memory_summaries').fetchone()[0],quiet)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_pending WHERE scope=?',(quiet,)).fetchone()[0],0)
            self.assertEqual(len(calls),1)
        finally:r.close();db.close()
    async def test_no_daily_summary_gate_and_busy_scopes_retry(self):
        db=sqlite3.connect(':memory:');r=Reflection(db);calls=[];gate=asyncio.Event()
        async def chat(**kw):
            calls.append(kw);await gate.wait();return NS(completion_text=json.dumps({'summary':'真实对话摘要'}))
        cfg={'memory':True,'memorySummary':True,'summaryRounds':30,'dailySummaryLimit':0,'naturalConversation':False}
        try:
            for scope in ('private:12345678','private:87654321'):
                for n in range(30):r.record(scope,scope[8:],str(n),'真实问题','真实回答')
            self.assertTrue(r.maybe_start('private:12345678',cfg,summary_provider(chat),lambda:True,{},contextvars.ContextVar('debt_event',default=None)))
            self.assertFalse(r.retry());gate.set();await r.task
            await asyncio.sleep(0)
            if not r.task or r.task.done():r.retry()
            await r.task
            self.assertEqual(len(calls),2);self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],2)
        finally:r.close();db.close()
    async def test_failed_debt_survives_restart_and_no_duplicate_partial_round(self):
        db=sqlite3.connect(':memory:');r=Reflection(db);scope='private:12345678'
        for n in range(30):r.record(scope,'12345678',str(n),'真实问题','真实回答')
        async def fail(**kw):raise RuntimeError('provider unavailable')
        cfg={'memorySummary':True,'summaryRounds':30};var=contextvars.ContextVar('debt_event',default=None)
        try:
            r.maybe_start(scope,cfg,summary_provider(fail),lambda:True,{},var);await r.task
            self.assertFalse(r.retry());r.close();r=Reflection(db)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],30)
            r.incoming(scope,'12345678','partial','原问题');r.confirmed_prefix(scope,'12345678','partial','首条已发送',r.epoch())
            self.assertEqual(db.execute("SELECT answer FROM memory_pending WHERE event='partial'").fetchone()[0],'首条已发送')
            self.assertEqual(db.execute("SELECT count(*) FROM memory_rounds WHERE event='partial'").fetchone()[0],0)
            async def success(**kw):return NS(completion_text=json.dumps({'summary':'实际整理完成'}))
            db.execute('UPDATE memory_reflection_retry SET next_at=0');db.commit()
            r.maybe_start(scope,cfg,summary_provider(success),lambda:True,{},var);await r.task
            self.assertEqual(db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        finally:r.close();db.close()

if __name__=='__main__':unittest.main()
