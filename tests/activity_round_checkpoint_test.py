"""Real summary SDK wire with typed activations and consecutive checkpoints."""
import copy,json,sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import semantic_provider_wire_test as fixture


class ActivationCheckpoint(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.SemanticProviderWire.asyncSetUp
    asyncTearDown=fixture.SemanticProviderWire.asyncTearDown
    summarize=fixture.SemanticProviderWire.summarize

    async def test_service_material_never_becomes_a_human_statement(self):
        from context_protocol import SOURCE,model_json
        from summary_material import snapshots,derive_claims
        text=SOURCE+model_json({'scope':fixture.SCOPE,'sender_id':'90000000','event_id':'service','kind':'platform_service_not_user'})+'\nPlatform activation'
        native=[{'role':'user','content':text},{'role':'assistant','content':'Actual internal observation'}]
        captured=snapshots(native,fixture.SCOPE)[0]
        captured['reply']=''  # Internal generated text has no QQ receipt.
        payload=[{'event':'service','sender_id':'90000000','user':text,'bot':'','at':1}]
        claims=derive_claims(fixture.SCOPE,payload,{'service':captured},[],{'evidence':[{'event':'service','who':'user','quote':'Platform activation'},{'event':'service','who':'bot','quote':'Actual internal observation'}]})
        self.assertEqual([c['kind'] for c in claims],['bot_statement'])
        self.assertEqual(claims[0]['delivery'],'observed')

    def append(self,start,count):
        from activation_rounds import record
        from context_protocol import SOURCE,model_json
        from readonly_journal import Prepared,ReadonlyJournal
        from summary_material import snapshots,remember
        db=self.store.db;native=[{'role':'system','content':'STABLE_MAIN_PERSONA'}]
        for i in range(start,start+count):
            event=f'activation-{i}'
            db.execute('INSERT INTO pipeline_actions(id,scope,state,at) VALUES(?,?,?,?)',(event,fixture.SCOPE,'completed',time.time()))
            record(db,fixture.SCOPE,event,event,source_scope=fixture.SCOPE,subject='90000000')
            user={'role':'user','content':SOURCE+model_json({'scope':fixture.SCOPE,'sender_id':'90000000','event_id':event,'kind':'platform_service_not_user'})+'\nPlatform activation '+str(i)}
            assistant={'role':'assistant','content':'[NO_REPLY]' if i%2 else 'Actual internal observation '+str(i)}
            native.extend([user,assistant]);remember(db,fixture.SCOPE,snapshots([user,assistant],fixture.SCOPE))
        db.commit()
        journal=ReadonlyJournal(db,fixture.SCOPE);journal.serial+=1
        if journal.last:native=copy.deepcopy(journal.last.base)+native[1:]
        prepared=Prepared(journal.serial,None,copy.deepcopy(native),copy.deepcopy(native),{},1,time.time(),'initial')
        prepared.main_conversation=True
        self.assertTrue(journal.commit(prepared));self.store.model_journals[fixture.SCOPE]=journal

    async def test_34_35_36_and_two_legal_checkpoints(self):
        import contextvars
        r=self.store.reflection
        r.binding=(self.cfg,self.provider,lambda:True,self.metrics,contextvars.ContextVar('activation_checkpoint',default=None))
        self.append(1,34)
        self.assertFalse(r.retry());self.assertEqual(self.requests,[])
        self.append(35,1)
        await self.summarize()
        self.assertEqual(len(self.requests),1)
        prompt=next(json.loads(m['content']) for m in self.requests[0]['messages'] if isinstance(m.get('content'),str) and m['content'].startswith('{') and '"turns"' in m['content'])
        self.assertEqual(prompt['turns'],[])
        self.assertEqual(len(prompt['autonomous_activations']),35)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1,self.metrics)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints WHERE round_count=35').fetchone()[0],1)
        from readonly_journal import ReadonlyJournal
        from activity_context import canonical_main
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        restored=ReadonlyJournal(self.store.db,fixture.SCOPE)
        self.assertTrue(restored.last.main_conversation)
        self.store.model_journals.clear()
        with patch('bot_pipeline.delivery_scope',return_value=fixture.SCOPE):
            self.assertTrue(canonical_main(self.store,NS(get_extra=lambda key:None)))
        self.append(36,1);self.assertFalse(r.retry())
        self.append(37,34)
        await self.summarize()
        self.assertEqual(len(self.requests),2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],2,self.metrics)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints WHERE round_count=35').fetchone()[0],2)


if __name__=='__main__':unittest.main()
