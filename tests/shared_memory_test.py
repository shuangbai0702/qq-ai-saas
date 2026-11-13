import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from memory_reflection import Reflection
from memory_scope import fact_rows,summary_rows
from data_admin import run
from context_protocol import event_message
from summary_material import snapshots
from readonly_journal import Prepared,ReadonlyJournal
from summary_stream_fixture import summary_provider


class SharedMemory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.person='12345678'
        self.group='group:55555';self.private='private:'+self.person;self.now=time.time();self.n=0
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def say(self,text,scope=None,subject=None):
        self.n+=1
        scope=scope or self.group;subject=subject or self.person
        # These fixtures supply an already selected semantic fact. Natural
        # chat extraction is tested separately and remains disabled.
        if text.startswith('我喜欢喝'):
            status=self.store.memory.remember(scope,subject,'饮料偏好',text[len('我喜欢喝'):],'real-'+str(self.n),self.now+self.n,origin='self_report')
            self.db.commit()
            return {'facts':self.store.memory.recall(scope,subject,'喝什么',self.now+self.n),'memory_status':status}
        return self.store.observe(scope,subject,text,'real-'+str(self.n),now=self.now+self.n,auto_capture=True)
    def summary(self,text,scope,people=(),events=('old',)):
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,text,json.dumps(events),self.now)).lastrowid
        self.db.executemany('INSERT INTO memory_summary_people VALUES(?,?)',[(identifier,p) for p in people]);self.db.commit();return identifier
    def record_summary_batch(self,question,answer):
        history=[{'role':'system','content':'fixture policy'}]
        for i in range(35):
            event='round'+str(i)
            user=event_message({'scope':self.group,'sender_id':self.person,'event_id':event},question)
            reply={'role':'assistant','content':answer}
            history.extend([user,reply])
            self.store.reflection.record(self.group,self.person,event,question,answer,materials=snapshots([user,reply],self.group))
        journal=ReadonlyJournal(self.db,self.group);journal.serial=1
        if not journal.commit(Prepared(1,None,history,history,{},1,self.now,'initial')):
            raise AssertionError('35 confirmed fixture rounds must commit to the real Journal')
        self.store.model_journals[self.group]=journal
    def test_trusted_same_qq_group_private_group_and_other_people(self):
        self.say('我喜欢喝红茶')
        for scope in [self.private]:
            facts=self.say('我爱喝什么',scope)['facts'];self.assertEqual(facts[0]['value'],'红茶');self.assertEqual(facts[0]['source_scope'],self.group);self.assertEqual(facts[0]['subject'],self.person)
        self.assertEqual(self.say('喝什么','group:66666')['facts'][0]['value'],'红茶')
        self.assertEqual(self.say('喝什么',subject='87654321')['facts'],[])
        self.say('更正记忆：饮料偏好=咖啡',self.private)
        self.assertEqual(self.say('喝什么')['facts'][0]['value'],'咖啡')
        self.assertEqual(self.say('喝什么',self.private)['facts'][0]['value'],'咖啡')
        self.assertEqual(self.db.execute('SELECT count(*) FROM facts WHERE active=1').fetchone()[0],1)
    def test_other_bot_and_invalid_private_attribution_stay_isolated(self):
        self.say('记住：爱好=看鱼')
        other=BehaviorStore(Path(self.tmp.name)/'other.sqlite')
        try:self.assertEqual(other.memory.recall(self.private,self.person,'爱好',self.now),[])
        finally:other.close()
        self.assertEqual(self.store.memory.recall('private:87654321',self.person,'爱好',self.now),[])
    def test_cross_scope_forget_blocks_recapture_and_explicit_can_restore(self):
        self.say('我喜欢喝红茶');self.say('忘记：饮料偏好',self.private)
        for scope in [self.group,self.private,'group:66666']:
            result=self.say('我喜欢喝红茶',scope);self.assertEqual(result['facts'],[]);self.assertIn('不会自动重新保存',result['memory_status'])
        self.say('记住：饮料偏好=绿茶','group:66666')
        self.assertEqual(self.say('喝什么',self.private)['facts'][0]['value'],'绿茶')
    def test_shared_automatic_budget_and_version_retention(self):
        # Existing per-conversation usage still consumes today's shared quota.
        day=int((self.now+1)//86400)
        self.db.execute('INSERT INTO memory_capture_budget VALUES(?,?,?,19)',(self.group,self.person,day));self.db.commit()
        self.say('我喜欢喝旧预算');self.assertIn('额度',self.say('我喜欢喝白水',self.private)['memory_status'])
        self.db.execute('DELETE FROM memory_capture_budget');self.db.execute('DELETE FROM facts');self.db.commit()
        for i in range(20):self.say('我喜欢喝茶'+str(i),self.group if i%2 else self.private)
        self.assertIn('额度',self.say('我喜欢喝白水','group:66666')['memory_status'])
        for i in range(25):self.say('更正记忆：饮料偏好=茶'+str(i),self.group if i%2 else self.private)
        self.assertEqual(self.db.execute('SELECT count(*) FROM facts').fetchone()[0],10)
        self.assertEqual(self.say('喝什么',self.private)['facts'][0]['version'],45)
    def test_legacy_conflict_priority_and_no_destructive_migration(self):
        for scope,value,updated,origin in [(self.group,'手动红茶',1,'explicit'),(self.private,'自动咖啡',2,'self_report')]:
            self.db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,1,1)',(scope,self.person,'饮料偏好',value,scope,updated))
            self.db.execute('INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,?,?,.8)',(scope,self.person,'饮料偏好','stable',origin))
        self.db.commit();self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        self.assertEqual(self.say('喝什么',self.private)['facts'][0]['value'],'手动红茶')
        self.assertEqual(self.db.execute('SELECT count(*) FROM facts WHERE active=1').fetchone()[0],2)
        self.say('更正记忆：饮料偏好=白水',self.private)
        self.assertEqual(self.store.memory.recall(self.group,self.person,'白水',self.now)[0]['value'],'白水')
        self.assertEqual(self.store.memory.recall(self.private,self.person,'白水',self.now)[0]['value'],'白水')
        self.assertEqual(self.db.execute('SELECT count(*) FROM facts WHERE active=1').fetchone()[0],1)
    def test_admin_edit_forget_and_stale_version_apply_to_shared_person(self):
        self.say('记住：饮料=红茶')
        data={'action':'edit_fact','scope':self.group,'subject':self.person,'name':'饮料','expectedVersion':1,'value':'咖啡'}
        self.assertTrue(run(self.path,data,self.db)['ok'])
        self.assertEqual(self.say('饮料',self.private)['facts'][0]['value'],'咖啡')
        self.assertEqual(run(self.path,data,self.db)['status'],409)
        self.assertTrue(run(self.path,{**data,'action':'forget_fact','expectedVersion':2},self.db)['ok'])
        self.assertEqual(self.say('饮料',self.private)['facts'],[])
    def test_legacy_summary_attribution_uses_events_not_model_ids(self):
        self.say('我们聊平江路夜景')
        good=self.summary('平江路夜景',self.group,events=('real-1',))
        fake=self.summary(self.person+'平江路秘密','group:66666')
        own_private=self.summary('平江路相机',self.private)
        other_private=self.summary('平江路别人私聊','private:87654321')
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        result=self.store.reflection.recall(self.private,'平江路',subject=self.person)
        self.assertEqual({x['id'] for x in result},{own_private});self.assertTrue(all('source_scope' in x for x in result))
        self.assertNotIn(fake,{r[0] for r in summary_rows(self.db,self.private,self.person)})
        self.assertNotIn(other_private,{r[0] for r in summary_rows(self.db,self.private,self.person)})
        self.assertEqual({r['id'] for r in self.store.reflection.recall('private:87654321','平江路',subject='87654321')},{other_private})
    def test_fact_recall_and_checkpoint_foundation_share_authoritative_sources(self):
        self.say('记住：职业=木雕师',self.private)
        good=self.summary('木雕展览的未完话题',self.private,[self.person])
        bad=self.summary('木雕另一个人的秘密','private:87654321',['87654321'])
        facts=self.store.memory.recall(self.private,self.person,'职业',self.now)
        summaries=self.store.reflection.foundation(self.private)
        self.assertEqual(facts[0]['value'],'木雕师');self.assertEqual(facts[0]['source_scope'],self.private)
        self.assertIn(good,{x['id'] for x in summaries});self.assertNotIn(bad,{x['id'] for x in summaries})
        self.say('忘记：职业',self.group)
        self.assertEqual(self.store.memory.recall(self.private,self.person,'木雕师',self.now),[])
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_search'").fetchone())
    async def test_new_reflection_persists_real_participants_and_survives_restart(self):
        self.record_summary_batch('平江路夜景','夜景很好')
        async def text_chat(**kw):return NS(completion_text=json.dumps({'summary':'平江路夜景，提到87654321但此人未参与。'}))
        from semantic_runtime import finish_semantic_batches
        cfg={'memory':True,'memoryAutoCapture':True,'memorySummary':True,'selfDevelopment':True,'summaryRounds':35,'ownerUin':self.person}
        self.store.reflection.checkpoint_callback=lambda scope:finish_semantic_batches(self.store,cfg,scope)
        self.assertTrue(self.store.reflection.maybe_start(self.group,cfg,summary_provider(text_chat),lambda:True,{},contextvars.ContextVar('event',default=None)))
        await self.store.reflection.task
        self.assertEqual([r[0] for r in self.db.execute('SELECT subject FROM memory_summary_people')],[self.person])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds WHERE scope=?',(self.group,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material WHERE scope=?',(self.group,)).fetchone()[0],0)
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        self.assertFalse(self.store.reflection.recall(self.private,'夜景',subject=self.person))
        # Merely mentioning a QQ cannot disclose this group's history to it.
        # The actual participant above still recalls the group after restart.
        shared=self.store.reflection.recall('private:87654321','夜景',subject='87654321')
        self.assertEqual(shared,[])
        self.assertEqual([r[0] for r in self.db.execute('SELECT subject FROM memory_summary_people')],[self.person])
    async def test_late_reflection_cannot_restore_shared_forgotten_sources(self):
        self.say('记住：夜景=平江路')
        self.record_summary_batch('平江路','夜景')
        entered=asyncio.Event();release=asyncio.Event()
        async def text_chat(**kw):entered.set();await release.wait();return NS(completion_text=json.dumps({'summary':'平江路旧摘要'},ensure_ascii=False))
        self.assertTrue(self.store.reflection.maybe_start(self.group,{'memorySummary':True,'summaryRounds':35},summary_provider(text_chat),lambda:True,{},contextvars.ContextVar('event',default=None)))
        await asyncio.wait_for(entered.wait(),3);self.say('忘记：夜景',self.private);release.set();await self.store.reflection.task
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
    async def test_late_cross_scope_fact_vector_is_discarded(self):
        self.say('记住事件：爱好=红茶',self.private)
        entered=asyncio.Event();release=asyncio.Event()
        async def create(**kw):entered.set();await release.wait();return NS(data=[NS(index=i,embedding=[1.,0.]) for i in range(len(kw['input']))])
        client=NS(base_url='https://fixture.invalid',embeddings=NS(create=create));client.with_options=lambda **kw:client
        task=asyncio.create_task(self.store.memory.semantic_recall(self.private,self.person,'beverage',self.now,{'memorySemantic':True,'embeddingModel':'fixture'},NS(client=client),lambda:True))
        await entered.wait();self.say('更正记忆：爱好=咖啡',self.private);release.set()
        self.assertEqual(await task,[]);self.assertEqual(self.db.execute('SELECT count(*) FROM fact_vectors').fetchone()[0],0)
    async def test_shared_vectors_cached_under_original_scope_and_fenced_on_forget(self):
        self.say('记住事件：爱好=红茶',self.private);calls=[]
        async def create(**kw):calls.append(kw);return NS(data=[NS(index=i,embedding=[1.,0.]) for i in range(len(kw['input']))])
        client=NS(base_url='https://fixture.invalid',embeddings=NS(create=create));client.with_options=lambda **kw:client
        cfg={'memorySemantic':True,'embeddingModel':'fixture'}
        result=await self.store.memory.semantic_recall(self.private,self.person,'beverage',self.now,cfg,NS(client=client),lambda:True)
        self.assertEqual(result[0]['source_scope'],self.private)
        self.assertEqual(self.db.execute('SELECT scope FROM fact_vectors').fetchone()[0],self.private)
        before=len(calls)
        self.assertEqual((await self.store.memory.semantic_recall('group:66666',self.person,'beverage',self.now,cfg,NS(client=client),lambda:True))[0]['value'],'红茶')
        self.assertEqual(len(calls),before+1,'new request embeds its query, not the already cached fact')
        self.assertEqual(calls[-1]['input'],['beverage'])
        self.say('忘记：爱好',self.group);self.assertEqual(self.db.execute('SELECT count(*) FROM fact_vectors').fetchone()[0],0)
    async def test_summary_basis_is_attributed_query_independent_and_never_embeds(self):
        self.say('记住：夜景=平江路')
        good=self.summary('平江路夜景',self.private,[self.person])
        self.summary('另一个人的秘密夜景','private:87654321',['87654321'])
        calls=[]
        async def create(**kw):calls.append(kw);raise AssertionError('summary embedding is retired')
        client=NS(base_url='https://fixture.invalid',embeddings=NS(create=create));client.with_options=lambda **kw:client
        cfg={'memorySemantic':True,'embeddingModel':'fixture'}
        result=await self.store.reflection.semantic_recall(self.private,'night view',cfg,NS(client=client),lambda:True,{},subject=self.person)
        self.assertEqual([item['id'] for item in result],[good]);self.assertFalse(calls)
        self.say('忘记：夜景',self.private)
        self.assertEqual(await self.store.reflection.semantic_recall(self.private,'night view',cfg,NS(client=client),lambda:True,{},subject=self.person),[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)
        self.assertNotIn(good,{r[0] for r in summary_rows(self.db,self.group,self.person)})


if __name__=='__main__':unittest.main(verbosity=2)
