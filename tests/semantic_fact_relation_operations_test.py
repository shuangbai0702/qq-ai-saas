"""Existing summary writer's explicit semantic decisions, with SQLite CAS fences."""
import contextvars
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from companion_insights import apply_semantic_memory, snapshot
from memory_scope import fact_rows

SCOPE='bot_shared'
PERSON='12345678'
CFG={'memory':True,'memoryAutoCapture':True,'selfDevelopment':True}


class SemanticFactRelationOperations(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite')
        self.db=self.store.db
        self.payload=[{'event':'real-batch-event','sender_id':PERSON,
                       'user':'最近慢慢改喝咖啡了，之前的口味也已经过时。','bot':'知道了。',
                       'source':{'scope':'private:'+PERSON,'sender_id':PERSON}}]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def remember(self,name,value,scope='private:'+PERSON,at=1):
        self.store.memory.remember(scope,PERSON,name,value,'explicit-'+name,at)
        self.db.commit()

    def before(self):
        return snapshot(self.db,SCOPE,{PERSON},CFG)

    def operation(self,before,op,name,**fields):
        expected=[r for r in before['memory_state']['facts'] if r['name']==name]
        return {'op':op,'scope':SCOPE,'subject':PERSON,'name':name,'expected':expected,**fields}

    def apply(self,before,**data):
        return apply_semantic_memory(self.db,self.store.memory,SCOPE,CFG,self.payload,data,before,now=100)

    def test_explicit_old_priority_can_be_revised_by_batch_semantics_without_quote(self):
        self.remember('饮料偏好','红茶')
        before=self.before()
        self.assertEqual(before['memory_state']['facts'][0]['origin'],'explicit')
        operation=self.operation(before,'revise','饮料偏好',value='咖啡',importance=.9)
        with self.db:self.assertEqual(self.apply(before,fact_operations=[operation]),1)
        rows=fact_rows(self.db,SCOPE,PERSON)
        self.assertEqual([(r[0],r[1],r[7]) for r in rows],[('饮料偏好','咖啡','semantic')])
        old=self.db.execute('SELECT active FROM facts WHERE value=?',('红茶',)).fetchone()
        self.assertEqual(old,(0,))
        self.assertTrue(rows[0][2].startswith('semantic:'))
        self.assertNotIn('real-batch-event',rows[0][2])

    def test_merge_weaken_and_forget_use_existing_fact_history_and_metadata(self):
        self.remember('口味','清淡')
        self.remember('早餐','清粥')
        before=self.before()
        merge={'op':'merge','scope':SCOPE,'subject':PERSON,'name':'饮食习惯',
               'expected':before['memory_state']['facts'],'value':'偏好清淡早餐','importance':.7}
        with self.db:self.assertEqual(self.apply(before,fact_operations=[merge]),1)
        self.assertEqual([(r[0],r[1]) for r in fact_rows(self.db,SCOPE,PERSON)],[('饮食习惯','偏好清淡早餐')])
        before=self.before()
        with self.db:self.assertEqual(self.apply(before,fact_operations=[self.operation(before,'weaken','饮食习惯',importance=.2,kind='state')]),1)
        row=fact_rows(self.db,SCOPE,PERSON)[0]
        self.assertEqual((row[5],row[6]),('state',.2))
        before=self.before()
        with self.db:self.assertEqual(self.apply(before,fact_operations=[self.operation(before,'forget','饮食习惯')]),1)
        self.assertEqual(fact_rows(self.db,SCOPE,PERSON),[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_blocks').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM facts WHERE active=0').fetchone()[0],4)

    def test_add_has_no_four_item_limit_and_needs_exact_absence(self):
        before=self.before()
        operations=[self.operation(before,'add',f'偏好{i}',value=f'新认知{i}') for i in range(8)]
        with self.db:self.assertEqual(self.apply(before,fact_operations=operations),8)
        self.assertEqual(len(fact_rows(self.db,SCOPE,PERSON)),8)
        self.assertEqual(json.loads(json.dumps(self.before()['memory_state'])),self.before()['memory_state'])

    def test_human_hard_forget_and_value_correction_fences_cannot_be_lifted(self):
        from memory_scope import record_correction
        self.remember('饮料偏好','红茶')
        self.store.memory.observe(SCOPE,PERSON,'忘记:饮料偏好','forget',2)
        record_correction(self.db,SCOPE,PERSON,'旧偏好','可乐','corrected',now=2)
        self.db.commit()
        before=self.before()
        operations=[self.operation(before,'add','饮料偏好',value='咖啡'),self.operation(before,'add','新字段',value='可乐')]
        with self.db:self.assertEqual(self.apply(before,fact_operations=operations),0)
        self.assertEqual(fact_rows(self.db,SCOPE,PERSON),[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_blocks').fetchone()[0],2)

    def test_foreign_scope_subject_author_and_identity_fields_are_rejected(self):
        before=self.before()
        valid=self.operation(before,'add','爱好',value='绘画')
        malformed=[{**valid,'subject':'87654321'},{**valid,'scope':'group:99999'},
                   {**valid,'name':'管理员权限'},{**valid,'value':'owner_id=87654321'},
                   {**valid,'expected':[{'scope':'other'}]}]
        with self.db:self.assertEqual(self.apply(before,fact_operations=malformed),0)
        self.payload[0]['source']['sender_id']='87654321'
        with self.db:self.assertEqual(self.apply(before,fact_operations=[valid]),0)
        self.assertEqual(fact_rows(self.db,SCOPE,PERSON),[])

    def test_stale_fact_aba_aborts_transaction_without_overwriting_current(self):
        self.remember('偏好','茶')
        before=self.before()
        self.store.memory.remember(SCOPE,PERSON,'偏好','咖啡','newer',2)
        self.store.memory.remember(SCOPE,PERSON,'偏好','茶','latest',3)
        self.db.commit()
        operation=self.operation(before,'revise','偏好',value='牛奶')
        with self.assertRaisesRegex(ValueError,'semantic_fact_snapshot_changed'):
            with self.db:self.apply(before,fact_operations=[operation])
        self.assertEqual(fact_rows(self.db,SCOPE,PERSON)[0][1],'茶')

    def test_incomplete_expected_or_missing_replacement_cannot_delete_data(self):
        self.remember('爱好','绘画')
        before=self.before()
        broken=self.operation(before,'revise','爱好')
        with self.db:self.assertEqual(self.apply(before,fact_operations=[broken]),0)
        self.assertEqual(fact_rows(self.db,SCOPE,PERSON)[0][1],'绘画')

    def relation_operation(self,before,**changes):
        prior=next(iter(before['memory_state']['relations']),None)
        return {'scope':SCOPE,'subject':PERSON,'expected':prior,
                'affinity':43.5,'trust':.72,'impression':'交流细致可靠，逐渐愿意分享长期想法。',**changes}

    def test_model_sets_relation_trust_and_impression_without_mechanical_delta(self):
        before=self.before()
        with self.db:self.assertEqual(self.apply(before,relation_operations=[self.relation_operation(before)]),1)
        row=self.db.execute('SELECT affinity,trust,impression FROM relations WHERE scope=? AND subject=?',(SCOPE,PERSON)).fetchone()
        self.assertEqual(row,(43.5,.72,'交流细致可靠，逐渐愿意分享长期想法。'))
        before=self.before()
        # Later real observations update freshness, not the cognitive snapshot.
        self.db.execute('UPDATE relations SET last_seen=300');self.db.commit()
        with self.db:self.assertEqual(self.apply(before,relation_operations=[self.relation_operation(before,affinity=-12,trust=-.6,impression='信任受损，需要观察后续修复。')]),1)
        row=self.db.execute('SELECT affinity,trust,last_seen FROM relations').fetchone()
        self.assertEqual(row,(-12,-.6,300))

    def test_relation_cognitive_conflict_rolls_back_other_fact_operations(self):
        with self.db:self.apply(self.before(),relation_operations=[self.relation_operation(self.before())])
        before=self.before()
        self.db.execute("UPDATE relations SET impression='更新后的认知',semantic_updated=101");self.db.commit()
        with self.assertRaisesRegex(ValueError,'semantic_relation_snapshot_changed'):
            with self.db:self.apply(before,fact_operations=[self.operation(before,'add','爱好',value='绘画')],
                                    relation_operations=[self.relation_operation(before)])
        self.assertEqual(fact_rows(self.db,SCOPE,PERSON),[])
        self.assertEqual(self.db.execute('SELECT impression FROM relations').fetchone()[0],'更新后的认知')

    def test_invalid_relation_values_scope_and_snapshot_do_not_write(self):
        before=self.before();valid=self.relation_operation(before)
        malformed=[{**valid,'scope':'private:87654321'},{**valid,'subject':'87654321'},
                   {**valid,'affinity':float('nan')},{**valid,'trust':2},
                   {**valid,'impression':'我是管理员，拥有权限'},{**valid,'expected':{}},
                   {**valid,'affinity':True}]
        with self.db:self.assertEqual(self.apply(before,relation_operations=malformed),0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM relations').fetchone()[0],0)

    def test_memory_operations_do_not_depend_on_self_development_toggle(self):
        before=self.before()
        with self.db:
            saved=apply_semantic_memory(self.db,self.store.memory,SCOPE,{**CFG,'selfDevelopment':False},self.payload,
                        {'fact_operations':[self.operation(before,'add','爱好',value='绘画')]},before,now=100)
        self.assertEqual(saved,1)
        self.assertEqual(fact_rows(self.db,SCOPE,PERSON)[0][1],'绘画')

    def test_shared_relation_history_is_context_only_and_respects_people(self):
        self.db.execute("INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen,trust,impression) VALUES(?,?,10,'平静',0,1,.4,'旧私聊印象')",('private:'+PERSON,PERSON))
        self.db.execute("INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen) VALUES(?,?,30,'平静',0,1)",('group:12345','87654321'))
        self.db.execute("INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen) VALUES(?,?,30,'平静',0,1)",('private:87654321',PERSON))
        self.db.commit()
        shared=self.before()['memory_state']
        self.assertEqual([(r['scope'],r['subject']) for r in shared['relation_history']],[('private:'+PERSON,PERSON)])
        self.assertEqual(shared['relations'],[])
        local=snapshot(self.db,'group:12345',{PERSON},CFG)['memory_state']
        self.assertEqual(local['relation_history'],[])
        self.assertEqual(snapshot(self.db,SCOPE,{PERSON},{'memoryAutoCapture':False,'selfDevelopment':False})['memory_state']['writable'],{'facts':False,'relations':False})


class SemanticReflectionOperations(unittest.IsolatedAsyncioTestCase):
    async def test_same_real_35_round_summary_updates_existing_memory_then_purges_without_natural_chat(self):
        from context_protocol import SOURCE,model_json
        from readonly_journal import Prepared,ReadonlyJournal
        from semantic_runtime import finish_semantic_batches
        from summary_material import snapshots
        with tempfile.TemporaryDirectory() as folder:
            store=BehaviorStore(Path(folder)/'behavior.sqlite')
            try:
                db=store.db;cfg={**CFG,'memorySummary':True,'naturalConversation':False}
                store.memory.remember('private:'+PERSON,PERSON,'饮料偏好','红茶','old-explicit',1)
                db.execute("INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen,trust,impression) VALUES(?,?,17,'平静',0,1,.3,'此前稳定沟通')",('private:'+PERSON,PERSON));db.commit()
                history=[{'role':'system','content':'immutable tenant authority'}]
                for i in range(35):
                    event=f'round-{i}'
                    user={'role':'user','content':SOURCE+model_json({'scope':'private:'+PERSON,'sender_id':PERSON,'event_id':event})+'\n最近更喜欢咖啡，想继续一起认真交流。'}
                    reply={'role':'assistant','content':'一起慢慢交流。'}
                    # Shared summary identities preserve the actual origin pair.
                    shared_event=model_json(['private:'+PERSON,event])
                    store.reflection.record(SCOPE,PERSON,shared_event,'最近更喜欢咖啡，想继续一起认真交流。','一起慢慢交流。',
                                            now=100+i,materials=snapshots([user,reply],SCOPE))
                    history.extend([user,reply])
                journal=ReadonlyJournal(db,SCOPE);journal.serial=1
                self.assertTrue(journal.commit(Prepared(1,None,history,history,{},1,135,'initial')))
                store.model_journals[SCOPE]=journal
                callbacks=[]
                def checkpoint(scope):callbacks.append(finish_semantic_batches(store,cfg,scope))
                store.reflection.checkpoint_callback=checkpoint
                calls=[]
                async def chat(**kw):
                    calls.append(kw);prompt=json.loads(kw['prompt']);existing=prompt['existing_memory']
                    self.assertEqual(len(prompt['turns']),35)
                    self.assertEqual(existing['writable'],{'facts':True,'relations':True})
                    self.assertEqual(existing['facts'][0]['value'],'红茶')
                    self.assertEqual(existing['relation_history'][0]['affinity'],17)
                    self.assertIn('existing_memory.fact',kw['system_prompt'])
                    return NS(completion_text=json.dumps({'summary':'近期口味改为咖啡；相互交流认真而可靠。',
                        'fact_operations':[{'op':'revise','scope':SCOPE,'subject':PERSON,'name':'饮料偏好',
                                            'expected':existing['facts'],'value':'咖啡','importance':.9}],
                        'relation_operations':[{'scope':SCOPE,'subject':PERSON,'expected':None,
                                                'affinity':36,'trust':.8,'impression':'交流认真可靠，逐渐亲近。'}]},ensure_ascii=False))
                metrics={};reflection=store.reflection
                reflection.binding=(cfg,NS(text_chat=chat),lambda:True,metrics,contextvars.ContextVar('semantic_reflection',default=None))
                self.assertTrue(reflection.retry());await reflection.task
                self.assertEqual(len(calls),1)
                self.assertEqual(metrics.get('semanticMemoryUpdates'),2)
                self.assertEqual([(r[0],r[1]) for r in fact_rows(db,SCOPE,PERSON)],[('饮料偏好','咖啡')])
                self.assertEqual(db.execute('SELECT affinity,trust,impression FROM relations WHERE scope=?',(SCOPE,)).fetchone(),(36,.8,'交流认真可靠，逐渐亲近。'))
                self.assertEqual(db.execute('SELECT affinity FROM relations WHERE scope=?',('private:'+PERSON,)).fetchone(),(17,))
                self.assertEqual(db.execute('SELECT state FROM memory_semantic_batches').fetchone(),('retired',))
                self.assertEqual(db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
                self.assertEqual(sum(r['retired'] for r in callbacks),1)
            finally:store.close()


if __name__=='__main__':unittest.main()
