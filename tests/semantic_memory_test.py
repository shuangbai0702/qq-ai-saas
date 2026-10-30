import asyncio,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'))
from memory_reflection import Reflection,invalidate

class SemanticMemory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.r=Reflection(self.db);self.calls=[]
        for scope,text in [('group:1','喜欢喝红茶'),('group:1','明天去爬山'),('group:2','别的群的秘密')]:
            self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,text,'[]',1))
        self.db.commit();self.cfg={'memorySemantic':True,'embeddingModel':'bge-m3'}
        async def create(**kw):
            self.calls.append(kw)
            return NS(data=[NS(index=i,embedding=[0.,1.] if '爬山' in text else [1.,0.]) for i,text in enumerate(kw['input'])])
        self.client=NS(base_url='https://models.example/v1',embeddings=NS(create=create))
        self.client.with_options=lambda **kw:self.client
        self.provider=NS(client=self.client)
    def tearDown(self):self.r.close();self.db.close()
    async def recall(self,**kw):
        return await self.r.semantic_recall('group:1','饮品',self.cfg,self.provider,kw.get('current',lambda:True),{})
    async def test_semantic_isolation_and_cache(self):
        result=await self.recall();self.assertEqual([x['summary'] for x in result],['喜欢喝红茶'])
        self.assertEqual(len(self.calls[0]['input']),3)
        await self.recall();self.assertEqual(len(self.calls[-1]['input']),1)
        self.client.base_url='https://other.example/v1';await self.recall()
        self.assertEqual(len(self.calls[-1]['input']),3)
    async def test_disabled_budget_and_failure_fallback(self):
        self.cfg['dailyEmbeddingLimit']=0;self.assertTrue(await self.recall());self.assertFalse(self.calls)
        self.cfg['dailyEmbeddingLimit']=100
        async def broken(**kw):raise ValueError('unsupported')
        self.client.embeddings.create=broken
        self.assertTrue(await self.recall())
        self.cfg['memorySemantic']=False;self.assertTrue(await self.recall())
    async def test_invalid_vectors_fallback(self):
        async def malformed(**kw):return NS(data=[NS(index=i,embedding=[float('nan')]) for i in range(len(kw['input']))])
        self.client.embeddings.create=malformed
        self.assertTrue(await self.recall());self.assertEqual(self.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)
    async def test_missing_embedding_client_falls_back(self):
        self.provider=NS()
        self.assertTrue(await self.recall())
    async def test_late_forget_stop_and_replacement(self):
        for mode in ('forget','stop','replace'):
            entered=asyncio.Event();release=asyncio.Event();valid=[True]
            async def delayed(**kw):
                entered.set();await release.wait()
                return NS(data=[NS(index=i,embedding=[1.,0.]) for i in range(len(kw['input']))])
            self.client.embeddings.create=delayed
            task=asyncio.create_task(self.recall(current=lambda:valid[0]));await entered.wait()
            if mode=='forget':invalidate(self.db,'group:1')
            elif mode=='stop':self.r.close()
            else:valid[0]=False
            release.set();self.assertEqual(await task,[])
            self.assertEqual(self.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)
            self.r.closed=False
            self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',('group:1','喜欢喝红茶','[]',1));self.db.commit()
    async def test_forget_deletes_cached_vectors(self):
        await self.recall();invalidate(self.db,'group:1')
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)

class ProactiveSchedule(unittest.TestCase):
    def test_persisted_random_wait_and_unanswered_limit(self):
        import initiative
        db=sqlite3.connect(':memory:');initiative.initialize(db)
        db.execute('CREATE TABLE deliveries(scope TEXT,day TEXT,count INTEGER,last_sent REAL,PRIMARY KEY(scope,day))')
        now=1800000000
        cfg={'proactive':True,'behavior':{'proactiveTargets':['private:12345'],'quietStart':0,'quietEnd':0,'proactiveChance':1,'proactiveIntervalSeconds':60,'silenceSeconds':60,'proactiveRandomInterval':True,'proactiveMinMinutes':30,'proactiveMaxMinutes':600,'maxUnanswered':4}}
        initiative.incoming(db,'private:12345','12345','99999','你好',now)
        self.assertEqual(initiative.candidates(db,cfg,now+100,rng=lambda:0.5),[])
        due=db.execute('SELECT due FROM initiative_schedule').fetchone()[0]
        self.assertEqual(due,now+315*60)
        self.assertEqual(initiative.candidates(db,cfg,now+101,rng=lambda:0),[])
        self.assertEqual(db.execute('SELECT due FROM initiative_schedule').fetchone()[0],due)
        self.assertEqual(len(initiative.candidates(db,cfg,due,rng=lambda:0)),1)
        db.execute('UPDATE contacts SET unanswered=4');db.commit()
        self.assertEqual(initiative.candidates(db,cfg,due,rng=lambda:0),[])
        initiative.incoming(db,'private:12345','12345','99999','新的消息',due)
        self.assertEqual(initiative.candidates(db,cfg,due+100,rng=lambda:0),[])
        self.assertEqual(db.execute('SELECT due FROM initiative_schedule').fetchone()[0],due+1800)
        db.close()

if __name__=='__main__':unittest.main()
