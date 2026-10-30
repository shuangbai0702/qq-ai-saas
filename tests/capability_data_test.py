import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from data_admin import run
from social import maybe_learn
from contextvars import ContextVar


def seed(file):
    store=BehaviorStore(file)
    store.observe('group:12345','123456','记住：饮料=红茶','e1')
    store.observe('group:54321','654321','记住：生日=五月一日','e2')
    store.db.execute('INSERT INTO social_patterns VALUES(?,?,?,?,?,?)',('group:12345','有节目效果','朋友间轻松开玩笑','[1,2]',2,time.time()))
    store.db.commit()
    return store


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.file=Path(self.tmp.name)/'behavior.sqlite'
        self.store=seed(self.file)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def data(self,**filters):return run(self.file,{'action':'list',**filters})

    def test_scope_subject_and_query(self):
        rows=self.data(scope='group:12345',subject='123456',query='红茶')['facts']
        self.assertEqual(len(rows),1)
        self.assertEqual(self.data(scope='group:12345',subject='654321')['facts'],[])
        self.assertNotIn('path',json.dumps(self.data()))
        relation=self.data(scope='group:12345')['relations'][0]
        body={'action':'reset_relation','scope':relation['scope'],'subject':relation['subject'],'expectedLastSeen':relation['last_seen']}
        self.assertTrue(run(self.file,body)['ok'])
        self.assertEqual(run(self.file,body)['status'],409)
        self.assertEqual(len(self.data()['facts']),2)

    def test_manual_edit_is_persistent_and_conflicts(self):
        body={'action':'edit_fact','scope':'group:12345','subject':'123456','name':'饮料','expectedVersion':1,'value':'乌龙茶'}
        self.assertTrue(run(self.file,body)['ok'])
        self.assertEqual(run(self.file,body)['status'],409)
        self.assertEqual(self.data(scope='group:12345')['facts'][0]['value'],'乌龙茶')
        recalled=self.store.memory.recall('group:12345','123456','饮料',time.time())
        self.assertIn('乌龙茶',json.dumps(recalled,ensure_ascii=False))

    def test_forget_blocks_auto_relearning_but_allows_explicit_consent(self):
        body={'action':'forget_fact','scope':'group:12345','subject':'123456','name':'饮料','expectedVersion':1}
        self.assertTrue(run(self.file,body)['ok'])
        # A delayed replay of the original explicit message must not revoke a
        # later platform forget operation.
        self.store.memory.remember('group:12345','123456','饮料','红茶','e1',time.time());self.store.db.commit()
        self.store.memory.remember('group:12345','123456','饮料','红茶','e3',time.time(),origin='self_report');self.store.db.commit()
        self.assertEqual(self.data(scope='group:12345')['facts'],[])
        self.store.memory.remember('group:12345','123456','饮料','绿茶','e4',time.time());self.store.db.commit()
        self.assertEqual(self.data(scope='group:12345')['facts'][0]['value'],'绿茶')

    def test_disable_pattern_affects_runtime_and_blocks_late_upsert(self):
        p=self.data()['patterns'][0]
        body={'action':'set_pattern','scope':p['scope'],'expression':p['expression'],'expectedUpdated':p['updated'],'expectedEpoch':0,'disabled':True}
        self.assertTrue(run(self.file,body)['ok'])
        self.assertEqual(self.store.social.expressions(p['scope'],'开玩笑'),[])
        batch=[{'id':1,'sender':'a','text':'有节目效果'},{'id':2,'sender':'b','text':'有节目效果'}]
        raw=json.dumps({'patterns':[{'expression':'有节目效果','situation':'轻松开玩笑','source_ids':[1,2]}]})
        self.assertEqual(self.store.social.accept_learning(p['scope'],batch,raw),0)
        self.assertEqual(run(self.file,body)['status'],409)
        body.update(expectedEpoch=1,disabled=False)
        self.assertTrue(run(self.file,body)['ok'])
        self.assertEqual(len(self.store.social.expressions(p['scope'],'开玩笑')),1)

    def test_clear_keeps_facts_and_other_scope(self):
        self.store.social.observe('group:12345','1','好有节目效果','1')
        self.store.social.observe('group:54321','2','另一群的表达','2')
        self.assertTrue(run(self.file,{'action':'clear_learning','scope':'group:12345'})['ok'])
        self.assertEqual(self.data(scope='group:12345')['patterns'],[])
        self.assertEqual(len(self.data()['facts']),2)
        self.assertEqual(self.store.db.execute('SELECT scope FROM social_samples').fetchall(),[('group:54321',)])

    def test_independent_social_awareness(self):
        self.assertIn('participation_rule',self.store.social.context('group:12345','你好',{'socialAwareness':True}))
        self.assertEqual(self.store.social.context('group:12345','你好',{}),{})
        self.assertNotIn('participation_rule',self.store.social.context('group:12345','你好',{'socialAwareness':False,'styleLearning':True,'memeEnabled':True}))

    def test_disabled_image_excluded_from_runtime_catalog(self):
        root=Path(self.tmp.name)/'reactions';root.mkdir()
        (root/'test.png').write_bytes(b'image-fixture')
        (root/'test.json').write_text(json.dumps({'id':'test','meaning':'打招呼','enabled':False}))
        self.assertNotIn('test',[r['id'] for r in self.store.social.catalog()])

    def test_pausing_contact_respects_recipient_opt_out(self):
        import initiative
        initiative.incoming(self.store.db,'private:123456','123456','10000','你好',time.time())
        run(self.file,{'action':'pause_contact','scope':'private:123456'})
        self.assertEqual(self.data()['contacts'][0]['opted_out'],1)

    def test_clear_invalidates_inflight_model_result(self):
        async def exercise():
            ready,release=asyncio.Event(),asyncio.Event()
            class Provider:
                async def text_chat(self,**kwargs):
                    ready.set();await release.wait()
                    batch=json.loads(kwargs['prompt'])
                    return type('Response',(),{'completion_text':json.dumps({'patterns':[{'expression':'节目效果','situation':'轻松开玩笑','source_ids':[batch[0]['id'],batch[1]['id']]}]})})()
            for i in range(12):self.store.social.observe('group:12345',str(i%2),'真是有节目效果',str(i))
            maybe_learn(self.store.social,'group:12345',{'styleLearning':True},Provider(),lambda:True,{},ContextVar('event'))
            await ready.wait()
            run(self.file,{'action':'clear_learning','scope':'group:12345'})
            release.set();await self.store.social.task
            self.assertEqual(self.data(scope='group:12345')['patterns'],[])
        asyncio.run(exercise())


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--seed':
        seed(Path(sys.argv[2])).close()
    else:unittest.main()
