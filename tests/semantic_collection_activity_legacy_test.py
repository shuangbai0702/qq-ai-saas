"""Historical activation originals reach the existing legal batch in full."""
import json,sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'adapters/astrbot')]
import semantic_collection_test as fixture
import semantic_collection,summary_material
from context_protocol import SOURCE,model_json
from semantic_batch import event_key,commit
from readonly_journal import semantic_retire_projection

class ActivityCollection(unittest.TestCase):
    def setUp(self):self.case=fixture.CollectionTests();self.case.setUp();self.db=self.case.db
    def tearDown(self):self.case.tearDown()
    def test_oversized_activation_offers_exact_original_and_retires_whole_block(self):
        identifier='legacy-activation'
        text=SOURCE+model_json({'scope':'self','sender_id':'90000000','event_id':identifier,'at':50,'kind':'platform_service_not_user'})+'\n'+('OLD_GOAL_CONTEXT_'*22000)+'END_OF_COMPLETE_ORIGINAL'
        messages=[{'role':'user','content':text},{'role':'assistant','content':'Actual internal result'}]
        self.case.archive(messages,scope='bot_shared')
        captured=summary_material.snapshots(messages,'bot_shared',db=self.db)[0]
        self.assertFalse(captured['complete']);self.assertEqual(len(captured['text']),64000)
        collected=semantic_collection.collect(self.db,'bot_shared',100,[],discard_unavailable=True)
        self.assertEqual(len(collected),0);self.assertEqual(len(collected.discarded),1)
        full=collected.discarded[0]['available_observations'][0]
        self.assertEqual(full,messages)
        self.assertIn('END_OF_COMPLETE_ORIGINAL',model_json(collected.discarded))
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
        # Membership is granted only by the actual legal checkpoint commit.
        self.db.execute("INSERT INTO memory_reconstruction_checkpoints(id,scope,through_round_id,round_count,epoch,state,created) VALUES(1,'bot_shared',35,35,0,'available',100)")
        commit(self.db,'bot_shared',1,{},100,discarded=collected.discarded)
        permit={'semanticBatch':True,'scope':'bot_shared','batchMembers':[event_key('bot_shared',model_json(['self',identifier]))]}
        self.assertEqual(semantic_retire_projection(messages,permit),[])
    def test_oversized_later_activation_stays_outside_batch(self):
        text=SOURCE+model_json({'scope':'self','sender_id':'90000000','event_id':'later','at':101,'kind':'platform_service_not_user'})+'\n'+('LATER'*20000)
        self.case.archive([{'role':'user','content':text}],scope='bot_shared')
        collected=semantic_collection.collect(self.db,'bot_shared',100,[],discard_unavailable=True)
        self.assertEqual(collected,[]);self.assertEqual(collected.discarded,[])


import semantic_provider_wire_test as wire_fixture
class ActivityProviderWire(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=wire_fixture.SemanticProviderWire.asyncSetUp
    asyncTearDown=wire_fixture.SemanticProviderWire.asyncTearDown
    summarize=wire_fixture.SemanticProviderWire.summarize

    async def test_full_oversized_original_reaches_sdk_request_with_exact_35_anchor(self):
        from context_protocol import event_message
        db=self.store.db;self.db=db;reflection=self.store.reflection
        reflection.shared_context=True;reflection.checkpoint_callback=None
        for i in range(35):
            event=str(i)
            message=event_message({'scope':'private:12345678','sender_id':'12345678','event_id':event,'at':100+i},'Current dialogue '+event)
            key=model_json(['private:12345678',event])
            reflection.record('bot_shared','12345678',key,'Current dialogue '+event,'Confirmed reply',now=100+i,materials=summary_material.snapshots([message,{'role':'assistant','content':'Confirmed reply'}],'bot_shared',db=db))
        identifier='historical-service'
        text=SOURCE+model_json({'scope':'self','sender_id':'90000000','event_id':identifier,'at':50,'kind':'platform_service_not_user'})+'\n'+('ORIGINAL_CURRENT_GOALS_'*16000)+'END_OF_COMPLETE_SERVICE_ORIGINAL'
        messages=[{'role':'user','content':text},{'role':'assistant','content':'Actual internal activity result'}]
        db.execute("INSERT INTO pipeline_actions(id,scope,state,at) VALUES(?,'self','completed',50)",(identifier,))
        db.execute("INSERT INTO memory_message_sources VALUES('bot_shared',?,'activation',?)",(model_json(['self',identifier]),model_json({'round_kind':'autonomous_activation','owner_work':identifier,'source_scope':'self'})))
        db.executescript('CREATE TABLE memory_checkpoint_archives(scope,version,payload,sha); CREATE TABLE IF NOT EXISTS fishbot_readonly_journal_blobs(scope,sha,data,PRIMARY KEY(scope,sha));')
        fixture.CollectionTests.archive(self,messages,scope='bot_shared');db.commit()
        self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        await self.summarize()
        self.assertEqual(len(self.requests),1,self.metrics)
        prompt=next(json.loads(m['content']) for m in self.requests[0]['messages'] if isinstance(m.get('content'),str) and m['content'].startswith('{') and '"turns"' in m['content'])
        self.assertEqual(len(prompt['turns']),35)
        self.assertEqual(prompt['incomplete_material'][0]['available_observations'][0][0]['content'],text)
        self.assertIn('END_OF_COMPLETE_SERVICE_ORIGINAL',model_json(self.requests[0]['messages']))
        self.assertNotIn(identifier,model_json(prompt['turns']))
        self.assertEqual(db.execute("SELECT round_count FROM memory_reconstruction_checkpoints WHERE scope='bot_shared'").fetchone()[0],35)
        members=json.loads(db.execute('SELECT members FROM memory_semantic_batches').fetchone()[0])
        self.assertIn(event_key('bot_shared',model_json(['self',identifier])),members)

if __name__=='__main__':unittest.main()
