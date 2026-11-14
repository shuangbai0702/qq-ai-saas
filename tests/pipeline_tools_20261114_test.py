"""Original SQLite Work/receipt ownership; no SDK, model or external network."""
import copy,json,sqlite3,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline
import pipeline_tools as tools


class ToolWork(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.p=BotPipeline(self.db);tools.initialize(self.db)
        self.settings={'botId':'bot','tenantId':'tenant','generation':1,'revision':1}
        self.id={**self.settings,'id':'parent','cycleId':'parent','epoch':2,'kind':'research','expiresAt':(time.time()+300)*1000}
        self.p.bind_internal(SimpleNamespace(get_self_id=lambda:'12345',set_extra=lambda *x:None),self.settings,self.id,'self','自由选择')
        self.record={'bot_id':'bot','tenant_id':'tenant','generation':1,'config_revision':1,'work_id':'parent','step_id':'step','args_digest':'hash','operation':'analyze','args':{'bvid':'BV1111111111'},'write':False,'authority':'autonomous','mode_epoch':2,'settings_revision':1,'deadlineAt':(time.time()+300)*1000,'continuation':{}}
    def tearDown(self):self.db.close()
    def admit(self,**changes):return tools.admit(self.p,self.settings,{**self.record,**changes})
    def result(self,id,status='not_applicable'):
        return {'schemaVersion':'fishbot.tool-result.v1','actionId':id,'execution':'completed','receipt':{'status':status},'usage':{'modelRequests':1},'versions':{'adapter':'bilibili-web-v1'}}
    def test_original_work_only_exact_identity_and_analysis_deduplication(self):
        before={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        job=self.admit();self.assertEqual(job['state'],'queued')
        self.assertEqual(self.admit()['id'],job['id']);self.assertEqual(self.admit(step_id='another-step')['id'],job['id'])
        self.assertNotEqual(self.admit(step_id='different',args_digest='different',args={'bvid':'BV2222222222'})['id'],job['id'])
        with self.assertRaisesRegex(ValueError,'identity_conflict'):self.admit(args_digest='forged')
        with self.assertRaisesRegex(ValueError,'identity_tenant'):self.admit(tenant_id='other')
        self.assertEqual(before,{r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_events WHERE source='autonomous'").fetchone()[0],1)
    def test_claim_expiry_before_dispatch_can_resume_but_model_checkpoint_cannot(self):
        job=self.admit();claimed=tools.claim(self.p,'owner');self.assertEqual(claimed['id'],job['id'])
        self.assertIsNone(tools.claim(self.p,'other'))
        self.db.execute("UPDATE pipeline_events SET lease_until=? WHERE source='tool'",(time.time()-1,))
        resumed=tools.claim(self.p,'replacement');self.assertEqual(resumed['id'],job['id'])
        with self.assertRaisesRegex(ValueError,'lease_stale'):tools.advance(self.p,job['id'],'owner','checkpoint',{'usage':{'modelRequests':1}})
        tools.advance(self.p,job['id'],'replacement','checkpoint',{'usage':{'modelRequests':1}})
        self.db.execute("UPDATE pipeline_events SET lease_until=? WHERE source='tool'",(time.time()-1,))
        self.assertIsNone(tools.claim(self.p,'new'))
        lost=tools.view(tools.row(self.db,job['id']));self.assertEqual(lost['state'],'unknown');self.assertEqual(lost['completion_state'],'pending')
        self.assertTrue(tools.advance(self.p,job['id'],'replacement','finish',{'result':self.result(job['id'])}))
        self.assertEqual(tools.view(tools.row(self.db,job['id']))['state'],'finished')
    def test_dispatched_write_never_replayed_and_late_receipt_uses_original_owner(self):
        job=self.admit(operation='comment',write=True,args={'aid':'123','text':'real'},args_digest='comment')
        tools.claim(self.p,'owner');tools.advance(self.p,job['id'],'owner','dispatch',{})
        self.db.execute("UPDATE pipeline_events SET lease_until=? WHERE source='tool'",(time.time()-1,))
        self.assertIsNone(tools.claim(self.p,'new'))
        with self.assertRaisesRegex(ValueError,'write_unknown'):self.admit(operation='comment',step_id='new',write=True,args_digest='comment')
        self.assertFalse(tools.advance(self.p,job['id'],'not-owner','finish',{'result':self.result(job['id'],'confirmed')}))
        result=self.result(job['id'],'confirmed');result['receipt']['platformObjectId']='456'
        self.assertTrue(tools.advance(self.p,job['id'],'owner','finish',{'result':result}))
        receipt=self.db.execute('SELECT state,receipt FROM pipeline_action_effects WHERE action=?',(job['id'],)).fetchone()
        self.assertEqual(receipt[0],'confirmed');self.assertEqual(json.loads(receipt[1])['platformObjectId'],'456');self.assertNotIn('text',receipt[1])
    def test_forged_parent_and_expired_authority_rejected(self):
        for patch in ({'work_id':'missing'},{'mode_epoch':3},{'deadlineAt':(time.time()-1)*1000}):
            with self.assertRaises(ValueError):self.admit(**patch)
        self.db.execute("UPDATE pipeline_events SET status='completed' WHERE source='autonomous'")
        with self.assertRaisesRegex(ValueError,'parent_stale'):self.admit()

    def test_legal_batch_retires_consumed_result_only_and_keeps_real_receipt(self):
        from context_protocol import scoped_event_hash,model_json
        job=self.admit(operation='comment',write=True,args={'aid':'123','text':'private request'},args_digest='write')
        tools.claim(self.p,'owner');tools.advance(self.p,job['id'],'owner','dispatch',{})
        result=self.result(job['id'],'confirmed');result['receipt']['platformObjectId']='456'
        result['data']={'private':'old raw result'}
        tools.advance(self.p,job['id'],'owner','finish',{'result':result})
        wrong={scoped_event_hash('bot_shared',model_json(['private:99','parent']))}
        self.assertEqual(tools.retire_batch(self.db,'bot_shared',wrong),0)
        right={scoped_event_hash('bot_shared',model_json(['self','parent']))}
        self.assertEqual(tools.retire_batch(self.db,'bot_shared',right),1)
        native=tools.row(self.db,job['id']);self.assertIsNone(native['request']);self.assertIsNone(native['result'])
        self.assertTrue(tools.handle(self.p,self.settings,'receipt',{'operation':'comment','objectId':'456','accountUid':None,'aid':'123'})['confirmed'])
        self.assertEqual(tools.retire_batch(self.db,'bot_shared',right),0)

    def test_async_result_uses_consumer_batch_not_original_parent(self):
        from context_protocol import scoped_event_hash,model_json
        job=self.admit();tools.claim(self.p,'owner')
        result=self.result(job['id']);result['usage']['providerUsage']={'total_tokens':42};result['data']={'analysis':'raw analysis must retire'}
        tools.advance(self.p,job['id'],'owner','finish',{'result':result})
        old={scoped_event_hash('bot_shared',model_json(['self','parent']))}
        self.assertEqual(tools.retire_batch(self.db,'bot_shared',old),0)
        done=tools.completion(self.p,self.settings,{'authority':'autonomous','epoch':2,'settingsRevision':1,'consumerWorkId':'consumer'})
        tools.acknowledge(self.p,job['id'],done['owner'],'done','consumer')
        self.assertEqual(tools.retire_batch(self.db,'bot_shared',old),0)
        correct={scoped_event_hash('bot_shared',model_json(['self','consumer']))}
        self.assertEqual(tools.retire_batch(self.db,'bot_shared',correct),1)
        self.assertIsNone(tools.view(tools.row(self.db,job['id'])))
        receipt=tools.handle(self.p,self.settings,'budget_receipt',{'id':job['id']})
        self.assertEqual(receipt['owner'],'owner');self.assertEqual(receipt['usage']['providerUsage']['total_tokens'],42)
        self.assertNotIn('raw analysis',json.dumps(tools.row(self.db,job['id'])))
    def test_foreground_sender_scope_and_work_are_actual_parent_identity(self):
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES('private:12345','12345','message',?,'message','model_tools','received','')",(time.time(),))
        import hashlib
        work='foreground:'+hashlib.sha256(json.dumps(['bot','private:12345','12345','message'],ensure_ascii=False).encode()).hexdigest()
        job=self.admit(authority='foreground',mode_epoch=None,sender_id='12345',origin_scope='private:12345',origin_event='message',work_id=work)
        self.assertEqual(job['origin_scope'],'private:12345')
        with self.assertRaises(ValueError):self.admit(authority='foreground',mode_epoch=None,sender_id='77777',origin_scope='private:12345',origin_event='message',work_id=work,step_id='forged')
    def test_cancel_before_admit_and_restarted_work_do_not_create_dispatch(self):
        tools.cancel(self.p,self.settings,'parent','step')
        with self.assertRaisesRegex(ValueError,'canceled'):self.admit()
        job=self.admit(step_id='other');tools.claim(self.p,'owner')
        restarted=BotPipeline(self.db)
        self.assertEqual(tools.view(tools.row(self.db,job['id']))['state'],'claimed')
        self.assertIsNone(tools.claim(restarted,'other-owner'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_action_effects').fetchone()[0],0)
    def test_completion_lease_is_original_work_bound_and_missing_ack_not_success(self):
        job=self.admit();tools.claim(self.p,'executor');tools.advance(self.p,job['id'],'executor','finish',{'result':self.result(job['id'])})
        claim={'authority':'autonomous','epoch':2,'settingsRevision':1,'consumerWorkId':'new-cycle'}
        item=tools.completion(self.p,self.settings,claim);self.assertEqual(item['id'],job['id'])
        self.assertIsNone(tools.completion(self.p,self.settings,claim))
        self.assertFalse(tools.acknowledge(self.p,job['id'],'wrong','done')['updated'])
        value=tools.row(self.db,job['id']);value['request']['completion']['until']=time.time()-1;tools.save_request(self.db,value)
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,execution_started) VALUES('self','service','new-cycle',?,'autonomous','model_tools','received','',1)",(time.time(),))
        self.assertIsNone(tools.completion(self.p,self.settings,claim))
        self.assertEqual(tools.view(tools.row(self.db,job['id']))['completion_state'],'unknown')
        self.assertTrue(tools.acknowledge(self.p,job['id'],item['owner'],'done','new-cycle')['updated'])
        self.assertEqual(tools.view(tools.row(self.db,job['id']))['completion_state'],'done')

    def test_changed_configuration_never_replays_completion_and_retires_at_legal_parent(self):
        import semantic_batch
        from context_protocol import scoped_event_hash,model_json
        semantic_batch.initialize(self.db)
        job=self.admit();tools.claim(self.p,'owner')
        tools.advance(self.p,job['id'],'owner','finish',{'result':self.result(job['id'])})
        newer={**self.settings,'revision':2}
        self.assertEqual(tools.handle(self.p,newer,'pending',{'settingsRevision':1})['pending'],[])
        self.assertEqual(tools.view(tools.row(self.db,job['id']))['completion_state'],'stale')
        self.assertIsNotNone(tools.row(self.db,job['id'])['request'],'no legal boundary yet')
        members={scoped_event_hash('bot_shared',model_json(['self','parent']))}
        self.assertEqual(tools.retire_batch(self.db,'bot_shared',members),1)
        self.assertIsNone(tools.row(self.db,job['id'])['request'])

    def test_late_ack_after_exact_consumer_retirement_drops_raw_result(self):
        import semantic_batch
        from context_protocol import scoped_event_hash,model_json
        semantic_batch.initialize(self.db)
        job=self.admit();tools.claim(self.p,'owner')
        result=self.result(job['id']);result['data']={'analysis':'raw private analysis'}
        tools.advance(self.p,job['id'],'owner','finish',{'result':result})
        item=tools.completion(self.p,self.settings,{'authority':'autonomous','epoch':2,'settingsRevision':1,'consumerWorkId':'consumer'})
        members=[scoped_event_hash('bot_shared',model_json(['self','consumer']))]
        self.db.execute("INSERT INTO memory_semantic_batches VALUES(1,'bot_shared','digest',?,?,'retired',?,?)",(json.dumps(members),time.time(),time.time(),time.time()))
        self.assertTrue(tools.acknowledge(self.p,job['id'],item['owner'],'done','consumer')['updated'])
        self.assertIsNone(tools.row(self.db,job['id'])['request'])
        self.assertNotIn('raw private analysis',json.dumps(tools.row(self.db,job['id'])))


if __name__=='__main__':unittest.main()
