import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'))
from behavior import BehaviorStore
from companion_insights import apply,apply_semantic_memory,snapshot
from data_admin import run
from context_protocol import SOURCE,model_json
from summary_material import snapshots
from readonly_journal import Prepared,ReadonlyJournal
from summary_stream_fixture import summary_provider
import companion_state,emotion_state,qq_actions


class NaturalConversation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.now=time.time()
        self.cfg={'memory':True,'memoryAutoCapture':True,'memorySummary':True,'summaryRounds':35,'selfDevelopment':True,'naturalConversation':True}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def fact(self,name,value,scope='group:1',subject='11111',event='fact'):
        self.store.memory.remember(scope,subject,name,value,event,self.now)
        self.db.commit()
    def summary(self,text,scope='group:1',at=1):
        self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,text,'[]',at));self.db.commit()
    def payload(self,text='最近我搬到了苏州，正在学昆曲。',reply='我喜欢昆曲的水磨腔。',event='one'):
        self.store.observe('group:1','11111',text,event,now=self.now)
        return [{'sender_id':'11111','event':event,'user':text,'bot':reply}]
    def extract(self,payload,data,before=None):
        before=before or snapshot(self.db,'group:1',{'11111'})
        with self.db:return apply(self.db,self.store.memory,'group:1',self.cfg,payload,data,before,self.now+1)
    def test_fixed_foundation_is_query_independent_and_has_no_scope_leak(self):
        self.summary('11111上次去平江路拍夜景，雨天还想再去。')
        for i in range(80):self.summary('无关项目的进度记录'+str(i),at=10+i)
        self.summary('平江路秘密约定',scope='group:2',at=200)
        summaries=self.store.reflection.foundation('group:1')
        self.assertIn('平江路',summaries[0]['summary']);self.assertNotIn('秘密',json.dumps(summaries,ensure_ascii=False))
        # Fixed checkpoint summaries remain complete and chronological even
        # when the question changes; legacy overflow never creates retrieval.
        self.assertEqual(summaries,self.store.reflection.foundation('group:1'))
        self.assertEqual(len(summaries),2)
        self.assertEqual({s['recall_reason'] for s in summaries},{'checkpoint_foundation'})
        other=self.store.reflection.foundation('group:1')
        self.assertEqual(other,summaries)
    def test_fact_recall_obeys_person_scope_correction_and_forget(self):
        self.fact('职业','木雕师');self.fact('职业','不应出现的私密职业',subject='22222')
        result=self.store.memory.recall('group:1','11111','木雕师',self.now);self.assertEqual(result[0]['value'],'木雕师')
        row=self.db.execute("SELECT version FROM facts WHERE subject='11111' AND active=1").fetchone()
        self.assertTrue(run(self.path,{'action':'forget_fact','scope':'group:1','subject':'11111','name':'职业','expectedVersion':row[0]},connection=self.db)['ok'])
        self.assertEqual(self.store.memory.recall('group:1','11111','木雕师',self.now),[])
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_search'").fetchone())
    def test_fact_recall_survives_restart_without_a_search_index(self):
        self.fact('爱好','昆曲');self.assertTrue(self.store.memory.recall('group:1','11111','昆曲',self.now))
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        self.assertEqual(self.store.memory.recall('group:1','11111','昆曲',self.now)[0]['value'],'昆曲')
        self.assertTrue(self.store.memory.recall('group:1','11111','昆曲',self.now))
    def test_semantic_extraction_requires_author_evidence(self):
        payload=self.payload()
        self.extract(payload,{'facts':[{'event':'one','name':'居住城市','value':'苏州','quote':'我搬到了苏州','kind':'stable'},
                                      {'event':'invented','name':'职业','value':'老师','quote':'我是老师'},
                                      {'event':'one','name':'称呼','value':'主人','quote':'最近我搬到了苏州'}]})
        self.assertEqual(self.db.execute("SELECT name,value FROM facts WHERE active=1").fetchall(),[('居住城市','苏州')])
    def test_extraction_never_overwrites_manual_or_newer_fact(self):
        payload=self.payload();self.fact('居住城市','杭州')
        before=snapshot(self.db,'group:1',{'11111'})
        self.extract(payload,{'facts':[{'event':'one','name':'居住城市','value':'苏州','quote':'我搬到了苏州'}]},before)
        self.assertEqual(self.db.execute("SELECT value FROM facts WHERE active=1").fetchone()[0],'杭州')
        before=snapshot(self.db,'group:1',{'11111'})
        self.fact('称呼','小林',event='later')
        self.extract(payload,{'facts':[{'event':'one','name':'称呼','value':'苏州','quote':'我搬到了苏州'}]},before)
        self.assertEqual(self.db.execute("SELECT value FROM facts WHERE name='称呼' AND active=1").fetchone()[0],'小林')
    def test_forgotten_value_cannot_return_under_renamed_field(self):
        self.fact('居住城市','苏州');run(self.path,{'action':'forget_fact','scope':'group:1','subject':'11111','name':'居住城市','expectedVersion':1},connection=self.db)
        payload=self.payload();self.extract(payload,{'facts':[{'event':'one','name':'住所','value':'苏州','quote':'我搬到了苏州'}]})
        self.assertEqual(self.db.execute('SELECT count(*) FROM facts WHERE active=1').fetchone()[0],0)
    def test_own_interest_is_from_bot_not_user(self):
        payload=self.payload()
        self.extract(payload,{'interests':[{'event':'one','topic':'昆曲','quote':'我喜欢昆曲的水磨腔。'}, {'event':'one','topic':'苏州','quote':'我搬到了苏州'}]})
        self.assertEqual(self.db.execute('SELECT topic,source FROM companion_interests').fetchall(),[('昆曲','bot_expression')])
        self.extract(payload,{'interests':[{'event':'one','topic':'昆曲','quote':'我喜欢昆曲的水磨腔。'}]})
        self.assertEqual(self.db.execute('SELECT hits FROM companion_interests').fetchone()[0],1)
        self.assertFalse(companion_state.context(self.db,{'behavior':self.cfg},'group:2',self.now)['interests'])
    def test_motive_can_complete_without_repeating_its_title(self):
        payload=self.payload(reply='下次我们聊昆曲。')
        self.extract(payload,{'motives':[{'event':'one','topic':'昆曲','quote':'下次我们聊昆曲。','status':'open'}]})
        payload=self.payload(text='刚才的已经讲清楚了，换个话题吧。',event='two')
        self.extract(payload,{'motives':[{'event':'two','topic':'昆曲','quote':'刚才的已经讲清楚了','status':'resolved'}]})
        self.assertEqual(self.db.execute('SELECT status FROM companion_motives').fetchone()[0],'resolved')
    def test_late_emotion_cannot_overwrite_new_interaction(self):
        payload=self.payload();before=snapshot(self.db,'group:1',{'11111'})
        emotion_state.observe(self.db,'group:1','你真蠢',self.now+1,True)
        self.extract(payload,{'emotion':{'event':'one','kind':'happy','intensity':2,'quote':'我搬到了苏州'}},before)
        self.assertEqual(emotion_state.context(self.db,'group:1',self.now+2)['kind'],'angry')
    def test_model_relation_operation_sets_overall_state_once(self):
        payload=self.payload(text='你这话让我很不舒服。');before=snapshot(self.db,'group:1',{'11111'})
        self.assertEqual(self.db.execute('SELECT affinity FROM relations').fetchone()[0],0,'ordinary text observation does not infer a relationship score')
        prior=next((row for row in before['memory_state']['relations'] if row['subject']=='11111'),None)
        relation_version=next((row['version'] for row in before['memory_state']['relation_versions'] if row['subject']=='11111'),None)
        operation={'op':'replace' if prior else 'create','scope':'group:1','subject':'11111','expected_version':relation_version,
                   'affinity':-12,'trust':-.6,'impression':'这次交流让我不舒服，之后需要看是否能修复。'}
        data={'relation_operations':[operation]}
        with self.db:apply_semantic_memory(self.db,self.store.memory,'group:1',self.cfg,payload,data,before,self.now+1)
        expected=(-12,-.6,'这次交流让我不舒服，之后需要看是否能修复。')
        self.assertEqual(self.db.execute('SELECT affinity,trust,impression FROM relations WHERE scope=? AND subject=?',('group:1','11111')).fetchone(),expected)
        with self.assertRaisesRegex(ValueError,'semantic_relation_snapshot_changed'):
            with self.db:apply_semantic_memory(self.db,self.store.memory,'group:1',self.cfg,payload,data,before,self.now+1)
        self.assertEqual(self.db.execute('SELECT affinity,trust,impression FROM relations WHERE scope=? AND subject=?',('group:1','11111')).fetchone(),expected)
    def test_feedback_is_evidenced_quiet_is_not_rejection(self):
        p=self.store.participation
        p.outgoing('group:1','雨下得真大',self.now-200,subject='11111',event='one')
        p.incoming('group:1','22222','今天开会',self.now)
        self.assertEqual(p.entry_factor('group:1'),1)
        self.assertEqual(p.context('group:1')['feedback'][0]['result'],'unobserved')
        payload=self.payload(text='别这么说，我不喜欢这个玩笑。',event='two')
        earlier={'sender_id':'11111','event':'one','user':'下雨了','bot':'雨下得真大'}
        self.extract([earlier]+payload,{'effects':[{'event':'one','reaction_event':'two','result':'rejected','quote':'我不喜欢这个玩笑'}]})
        self.assertEqual(p.context('group:1')['feedback'][0]['result'],'rejected')
        self.assertEqual(p.entry_factor('group:1'),1,'one rejection must not shut down the group')
    def test_contextual_candidate_does_not_need_lexical_overlap(self):
        p=self.store.participation;p.outgoing('group:1','你可以试试热茶',1000,subject='11111')
        cfg={'naturalConversation':True,'followupSeconds':120,'followupReplyProbability':.8}
        self.assertTrue(p.in_conversation('group:1','我也想听，接着说',cfg,1001))
        self.assertFalse(p.in_conversation('group:1','另一人的事',cfg,1001,other_target=True))
        self.assertFalse(p.in_conversation('group:2','我也想听',cfg,1001))
        self.assertFalse(p.in_conversation('group:1','我也想听',{**cfg,'followupSeconds':0},1001))
    async def test_delayed_combined_reflection_cannot_undo_forget(self):
        scope='group:12345';self.fact('居住城市','杭州',scope=scope)
        history=[{'role':'system','content':'fixture policy'}]
        for i in range(35):
            event=str(i)
            user={'role':'user','content':SOURCE+model_json({'scope':scope,'sender_id':'11111','event_id':event})+'\n我搬到了苏州'}
            reply={'role':'assistant','content':'我喜欢昆曲的水磨腔。'}
            history.extend([user,reply])
            self.store.reflection.record(scope,'11111',event,'我搬到了苏州','我喜欢昆曲的水磨腔。',materials=snapshots([user,reply],scope))
        journal=ReadonlyJournal(self.db,scope);journal.serial=1
        self.assertTrue(journal.commit(Prepared(1,None,history,history,{},1,self.now,'initial')))
        self.store.model_journals[scope]=journal
        before=snapshot(self.db,scope,{'11111'})
        fact_version=next(item['version'] for item in before['memory_state']['fact_versions'] if item['subject']=='11111' and item['name']=='居住城市')
        entered=asyncio.Event();release=asyncio.Event()
        async def chat(**kw):
            entered.set();await release.wait()
            data={'summary':'此人说搬去了苏州','fact_operations':[{'op':'replace','scope':scope,'subject':'11111','name':'居住城市','expected_version':fact_version,'value':'苏州','kind':'stable','importance':.8}]}
            return NS(completion_text=json.dumps(data,ensure_ascii=False))
        self.assertTrue(self.store.reflection.maybe_start(scope,self.cfg,summary_provider(chat),lambda:True,{},contextvars.ContextVar('event',default=None)))
        await asyncio.wait_for(entered.wait(),3)
        run(self.path,{'action':'forget_fact','scope':scope,'subject':'11111','name':'居住城市','expectedVersion':1},connection=self.db)
        release.set();await self.store.reflection.task
        self.assertEqual(self.db.execute('SELECT count(*) FROM facts WHERE active=1').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],0)
    async def test_delayed_relation_reset_and_closed_store(self):
        payload=self.payload(text='你这话让我很不舒服。');before=snapshot(self.db,'group:1',{'11111'})
        row=self.db.execute('SELECT last_seen FROM relations').fetchone()
        run(self.path,{'action':'reset_relation','scope':'group:1','subject':'11111','expectedLastSeen':row[0]},connection=self.db)
        self.extract(payload,{'relations':[{'event':'one','sentiment':-1,'quote':'让我很不舒服'}]},before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM relations').fetchone()[0],0)
    def test_qzone_tool_available_for_natural_owner_request_only(self):
        event=NS(get_sender_id=lambda:'11111',get_message_str=lambda:'今天这段挺适合放你空间，发出去吧',get_extra=lambda k,d=None:d)
        cfg={'behavior':{'ownerUin':'11111','qzone':True}}
        self.assertIn('publish',qq_actions.allowed(event,cfg))
        event.get_sender_id=lambda:'22222';self.assertEqual(qq_actions.allowed(event,cfg),set())
        event.get_sender_id=lambda:'11111';event.get_extra=lambda k,d=None:True if k=='_hosted_initiative' else d
        self.assertEqual(qq_actions.allowed(event,cfg),set())


if __name__=='__main__':unittest.main(verbosity=2)
