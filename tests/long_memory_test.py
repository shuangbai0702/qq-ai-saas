import asyncio,json,os,sqlite3,sys,unittest,contextvars,time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'),str(Path(__file__).resolve().parents[1]/'references'/'astrbot')]
from memory_reflection import Reflection,invalidate
from hosted_memory import ScopedMemory
from data_admin import run

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
    async def recall(self,query='drink',current=lambda:True):
        return await self.r.semantic_recall('group:1',query,self.cfg,self.provider,current,{})
    async def test_fixed_basis_is_bounded_without_indexing_library(self):
        self.summary('tea');self.summary('private secret','group:2')
        for i in range(20):self.summary('unrelated '+str(i))
        result=await self.recall()
        self.assertEqual([item['summary'] for item in result],['tea','unrelated 0'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)
        self.assertFalse(self.calls)
    async def test_query_and_embedding_config_cannot_change_summary_basis(self):
        self.summary('rare-topic');self.summary('tea')
        first=await self.recall('rare-topic');self.cfg['embeddingModel']='other'
        self.assertEqual(await self.recall('completely different'),first)
        self.assertEqual(self.r.recall('group:1','tea'),first)
        self.assertFalse(self.calls)
    async def test_summary_reader_never_constructs_independent_embedding_client(self):
        import model_egress
        self.summary('tea');self.cfg['embeddingBaseUrl']='https://embedding.example/v1'
        with patch.object(model_egress,'client',side_effect=AssertionError('unexpected embedding client')):
            self.assertTrue(await self.recall())
        self.assertFalse(self.calls)
    async def test_summary_read_has_no_awaited_embedding_resource(self):
        self.summary('tea')
        async def unsupported(**kw):raise AssertionError('unexpected embedding')
        self.client.embeddings.create=unsupported
        self.assertTrue(await self.recall())
        self.assertFalse(self.r.embedding_lock.locked())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)
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
        self.assertEqual(await self.recall(current=lambda:False),[])
        invalidate(self.db,'group:1');self.db.commit()
        self.assertEqual(await self.recall(),[])
        self.summary('new evidence');self.r.close()
        self.assertEqual(await self.recall(),[])
        self.assertFalse(self.calls)
    async def test_late_reflection_cannot_restore_deleted_sources(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def text_chat(**kw):entered.set();await release.wait();return NS(completion_text='真实话题摘要')
        for i in range(35):self.r.record('group:1','123',str(i),'question','answer')
        self.r.maybe_start('group:1',{'memorySummary':True,'summaryRounds':35},NS(text_chat=text_chat),lambda:True,{},contextvars.ContextVar('event',default=None))
        await entered.wait();self.facts.observe('group:1','123','更正记忆：称呼=新名字','correct',2);self.db.commit()
        release.set();await self.r.task
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
    async def test_forget_blocks_automatic_recapture(self):
        self.facts.observe('group:1','123','我喜欢喝红茶','1',1,True)
        self.facts.observe('group:1','123','忘记：饮料偏好','2',2,True)
        status=self.facts.remember('group:1','123','饮料偏好','红茶','3',3,origin='self_report')
        self.assertIn('不会自动重新保存',status)
        self.assertFalse(self.facts.recall('group:1','123','饮料',4))

class FactSemantics(unittest.IsolatedAsyncioTestCase):
    setUp=LongMemory.setUp
    tearDown=LongMemory.tearDown
    summary=LongMemory.summary
    recall=LongMemory.recall
    async def fact_recall(self,query='beverage',current=lambda:True):
        return await self.facts.semantic_recall('group:1','123',query,100,self.cfg,self.provider,current)
    def seed(self,name='爱好',value='红茶',scope='group:1',subject='123',at=1):
        self.facts.remember(scope,subject,name,value,name+str(at),at,kind='event');self.db.commit()
    async def test_fact_semantic_nonlexical_scope_person_and_version(self):
        self.seed();self.seed(value='其他人秘密',subject='456');self.seed(value='其他群秘密',scope='group:2')
        async def create(**kw):
            self.calls.append(kw)
            return NS(data=[NS(index=i,embedding=[1.,0.]) for i,_ in enumerate(kw['input'])])
        self.client.embeddings.create=create
        self.assertEqual(self.facts.recall('group:1','123','beverage',100),[])
        result=await self.fact_recall();self.assertEqual(result[0]['value'],'红茶');self.assertEqual(result[0]['recall_reason'],'semantic_match')
        self.assertEqual(result[0]['source_event'],'爱好1');self.assertEqual(result[0]['version'],1)
        self.assertEqual(self.calls[0]['input'],['beverage','爱好 红茶'])
        await self.fact_recall();self.assertEqual(len(self.calls[-1]['input']),1)
        self.seed(value='咖啡',at=2);result=await self.fact_recall()
        self.assertEqual(result[0]['value'],'咖啡');self.assertEqual(result[0]['version'],2);self.assertEqual(len(self.calls[-1]['input']),2)
    async def test_fact_incremental_budget_and_endpoint_spaces(self):
        for i in range(200):self.seed(name='item'+str(i),value='tea',at=i+1)
        for i in range(13):await self.fact_recall()
        self.assertEqual(self.db.execute('SELECT count(*) FROM fact_vectors').fetchone()[0],200)
        self.assertTrue(all(len(c['input'])<=17 for c in self.calls))
        await self.fact_recall();self.assertEqual(len(self.calls[-1]['input']),1)
        self.client.base_url='https://other.example/v1';await self.fact_recall();self.assertEqual(len(self.calls[-1]['input']),17)
        self.cfg['embeddingModel']='new-model';await self.fact_recall();self.assertEqual(len(self.calls[-1]['input']),17)
        self.assertLessEqual(self.db.execute('SELECT count(*) FROM fact_vectors').fetchone()[0],200)
        self.db.execute('DELETE FROM memory_embedding_budget');self.db.commit();self.cfg['dailyEmbeddingLimit']=1
        self.summary('tea');await self.recall();before=len(self.calls)
        await self.fact_recall();self.assertEqual(len(self.calls),before+1)
        before=len(self.calls)
        self.cfg['dailyEmbeddingLimit']=0;await self.fact_recall();self.assertEqual(len(self.calls),before)
        self.db.execute('DELETE FROM memory_embedding_budget');self.db.commit();self.cfg['dailyEmbeddingLimit']=1
        await self.fact_recall();before=len(self.calls);await self.recall();self.assertEqual(len(self.calls),before)
    async def test_fact_late_correction_delete_forget_close_replace_and_config(self):
        for mode in ('correct','delete','forget','close','replace','config','failure-delete','aba'):
            self.db.execute('DELETE FROM facts');self.db.commit();self.facts=ScopedMemory(self.db);self.seed()
            entered=asyncio.Event();release=asyncio.Event();valid=[True]
            async def delayed(**kw):
                entered.set();await release.wait()
                if mode=='failure-delete':raise ValueError('network failure')
                return NS(data=[NS(index=i,embedding=[1.,0.]) for i in range(len(kw['input']))])
            self.client.embeddings.create=delayed
            task=asyncio.create_task(self.fact_recall(current=lambda:valid[0]));await entered.wait()
            if mode=='correct':
                self.assertTrue(run(Path('unused'),{'action':'edit_fact','scope':'group:1','subject':'123','name':'爱好','expectedVersion':1,'value':'咖啡'},self.db)['ok'])
            elif mode=='forget':
                self.assertTrue(run(Path('unused'),{'action':'forget_fact','scope':'group:1','subject':'123','name':'爱好','expectedVersion':1},self.db)['ok'])
            elif mode in ('delete','failure-delete','aba'):
                self.db.execute('DELETE FROM facts');self.db.commit()
                if mode=='aba':self.seed()
            elif mode=='close':self.facts.close()
            elif mode=='replace':valid[0]=False
            else:self.cfg['embeddingModel']='changed'
            release.set();self.assertEqual(await task,[])
            self.assertEqual(self.db.execute('SELECT count(*) FROM fact_vectors').fetchone()[0],0)
            self.assertFalse(self.facts.embedding_lock.locked())
    async def test_fact_cached_delete_and_failure_lexical_fallback(self):
        self.seed()
        async def create(**kw):return NS(data=[NS(index=i,embedding=[1.,0.]) for i in range(len(kw['input']))])
        self.client.embeddings.create=create;self.assertTrue(await self.fact_recall())
        self.db.execute('UPDATE facts SET active=0');self.db.commit()
        self.assertEqual(await self.fact_recall(),[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM fact_vectors').fetchone()[0],0)
        self.seed(name='饮料',value='红茶',at=2)
        async def broken(**kw):raise ValueError('unsupported')
        self.client.embeddings.create=broken
        result=await self.fact_recall('红茶');self.assertEqual(result[0]['value'],'红茶');self.assertEqual(result[0]['recall_reason'],'topic_match')
        self.cfg['memory']=False;self.assertEqual(await self.fact_recall('红茶'),[])
        self.cfg['memory']=True;self.cfg['memorySemantic']=False
        self.assertEqual((await self.fact_recall('红茶'))[0]['value'],'红茶')
    async def test_fact_real_sdk_independent_endpoint(self):
        import httpx,model_egress
        self.seed();requests=[];clients=[]
        async def handler(request):
            requests.append(request);data=json.loads(request.content)
            return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.,0.],'object':'embedding'} for i,_ in enumerate(data['input'])],'model':data['model'],'object':'list','usage':{'prompt_tokens':1,'total_tokens':1}})
        def guarded(config):
            self.assertEqual(config,{'api_base':'http://localhost:11434/v1'})
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler));clients.append(client);return client
        self.cfg['embeddingBaseUrl']='http://localhost:11434/v1'
        with patch.object(model_egress,'client',side_effect=guarded):result=await self.fact_recall()
        self.assertEqual(result[0]['recall_reason'],'semantic_match')
        self.assertEqual(json.loads(requests[0].content)['input'],['beverage','爱好 红茶'])
        self.assertEqual(requests[0].headers['authorization'],'Bearer test-bot-key');self.assertTrue(clients[0].is_closed)

class ReflectionDebt(unittest.IsolatedAsyncioTestCase):
    async def test_busy_group_cannot_evict_another_conversation_before_summary(self):
        db=sqlite3.connect(':memory:');r=Reflection(db);now=time.time()
        quiet='private:12345678';busy='group:55555'
        try:
            for n in range(35):r.record(quiet,'12345678','quiet'+str(n),'安静会话的问题','已确认回答',now-100+n)
            for n in range(996):
                db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(busy,'87654321','busy'+str(n),'群聊消息','已确认回答',now+n/1000))
                db.execute('INSERT INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)',(busy,'87654321','busy'+str(n),'群聊消息','已确认回答',now+n/1000))
            db.commit();r.record(busy,'87654321','new-busy','新的群聊消息','已确认回答',now+1)
            for table in ('memory_pending','memory_rounds'):
                self.assertGreaterEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],1000)
                self.assertEqual(db.execute('SELECT count(*) FROM '+table+' WHERE scope=?',(quiet,)).fetchone()[0],35)
            calls=[]
            async def chat(**kw):calls.append(kw);return NS(completion_text='安静会话的真实摘要')
            r.maybe_start(busy,{'memorySummary':True,'summaryRounds':35,'naturalConversation':False},NS(text_chat=chat),lambda:True,{},contextvars.ContextVar('fair_debt',default=None));await r.task
            self.assertEqual(db.execute('SELECT scope FROM memory_summaries').fetchone()[0],quiet)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_pending WHERE scope=?',(quiet,)).fetchone()[0],0)
            self.assertEqual(len(calls),1)
        finally:r.close();db.close()
    async def test_no_daily_summary_gate_and_busy_scopes_retry(self):
        db=sqlite3.connect(':memory:');r=Reflection(db);calls=[];gate=asyncio.Event()
        async def chat(**kw):
            calls.append(kw);await gate.wait();return NS(completion_text='真实对话摘要')
        cfg={'memory':True,'memorySummary':True,'summaryRounds':35,'dailySummaryLimit':0,'naturalConversation':False}
        try:
            for scope in ('private:12345678','private:87654321'):
                for n in range(35):r.record(scope,scope[8:],str(n),'真实问题','真实回答')
            self.assertTrue(r.maybe_start('private:12345678',cfg,NS(text_chat=chat),lambda:True,{},contextvars.ContextVar('debt_event',default=None)))
            self.assertFalse(r.retry());gate.set();await r.task
            await asyncio.sleep(0)
            if not r.task or r.task.done():r.retry()
            await r.task
            self.assertEqual(len(calls),2);self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],2)
        finally:r.close();db.close()
    async def test_failed_debt_survives_restart_and_no_duplicate_partial_round(self):
        db=sqlite3.connect(':memory:');r=Reflection(db);scope='private:12345678'
        for n in range(35):r.record(scope,'12345678',str(n),'真实问题','真实回答')
        async def fail(**kw):raise RuntimeError('provider unavailable')
        cfg={'memorySummary':True,'summaryRounds':35};var=contextvars.ContextVar('debt_event',default=None)
        try:
            r.maybe_start(scope,cfg,NS(text_chat=fail),lambda:True,{},var);await r.task
            self.assertFalse(r.retry());r.close();r=Reflection(db)
            self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
            r.incoming(scope,'12345678','partial','原问题');r.confirmed_prefix(scope,'12345678','partial','首条已发送',r.epoch())
            self.assertEqual(db.execute("SELECT answer FROM memory_pending WHERE event='partial'").fetchone()[0],'首条已发送')
            self.assertEqual(db.execute("SELECT count(*) FROM memory_rounds WHERE event='partial'").fetchone()[0],0)
            async def success(**kw):return NS(completion_text='实际整理完成')
            db.execute('UPDATE memory_reflection_retry SET next_at=0');db.commit()
            r.maybe_start(scope,cfg,NS(text_chat=success),lambda:True,{},var);await r.task
            self.assertEqual(db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        finally:r.close();db.close()

if __name__=='__main__':unittest.main()
