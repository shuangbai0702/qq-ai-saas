"""Production defect contracts: offline, no provider or protocol calls."""
import asyncio,json,sys,tempfile,time,unittest,sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from bot_pipeline import Work
import agreement_state as agreements

class Agreements(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.addCleanup(self.store.close)
        self.db=self.store.db;self.scope='private:12345678'
        self.lease={'tenantId':'tenant','botId':'bot','generation':1,'revision':2}
        self.identity={**self.lease,'epoch':1}
        self.work=Work('bot',self.scope,'12345678','87654321','event','message',time.time(),self.lease)
        self.work.ledger_owner=True
    def propose(self):
        row,error=agreements.apply_update(self.db,'propose','明天提醒带钥匙','12345678',['12345678','bot'],self.scope,'source')
        self.assertEqual(error,'');return row
    def accept(self,row):
        row,error=agreements.apply_update(self.db,'accept',row['topic'],'bot',['bot'],self.scope,'source',expected_revision=1)
        self.assertEqual(error,'');return row
    def test_confirmation_cas_and_unauthorized_participant(self):
        row=self.propose()
        _,error=agreements.apply_update(self.db,'accept',row['topic'],'12345678',['12345678'],self.scope,'source')
        self.assertEqual(error,'other_party_confirmation_required')
        self.accept(row)
        _,error=agreements.apply_update(self.db,'cancel',row['topic'],'22222222',['22222222'],self.scope,'source')
        self.assertEqual(error,'speaker_not_participant')
        _,error=agreements.apply_update(self.db,'cancel',row['topic'],'12345678',['12345678'],self.scope,'source',expected_revision=1)
        self.assertEqual(error,'agreement_revision_changed')
    def test_task_outbox_cancel_receipt_and_unknown_no_replay(self):
        row=self.accept(self.propose());binding={'id':row['id'],'revision':2}
        request={'objective':'在本私聊提醒带钥匙','delaySeconds':60,'agreement':binding}
        result=self.store.pipeline.propose_handoff(self.work,self.identity,request)
        self.assertEqual(result,self.store.pipeline.propose_handoff(self.work,self.identity,request))
        self.assertEqual(agreements.task_states(self.db)[0]['handoffId'],result['requestId'])
        agreements.settle_task(self.db,result['requestId'],{'status':'unknown','effects':[{'status':'unknown'}]})
        self.assertTrue(agreements.task_current(self.db,binding))
        agreements.apply_update(self.db,'cancel',row['topic'],'12345678',['12345678'],self.scope,'source',expected_revision=2)
        self.assertFalse(agreements.task_current(self.db,binding))
        agreements.settle_task(self.db,result['requestId'],{'status':'completed','effects':[{'status':'confirmed'}]})
        self.assertEqual(self.db.execute('SELECT status FROM memory_agreements').fetchone()[0],'canceled')
    def test_fulfillment_needs_clean_actual_result(self):
        row=self.accept(self.propose())
        result=self.store.pipeline.propose_handoff(self.work,self.identity,{'objective':'提醒','delaySeconds':0,'agreement':{'id':row['id'],'revision':2}})
        agreements.settle_task(self.db,result['requestId'],{'status':'completed','effects':[{'status':'confirmed'}],'unfinished':['仍未完成']})
        self.assertEqual(self.db.execute('SELECT status FROM memory_agreements').fetchone()[0],'accepted')
        agreements.settle_task(self.db,result['requestId'],{'status':'completed','effects':[{'status':'confirmed'}],'unfinished':[]})
        self.assertEqual(self.db.execute('SELECT status FROM memory_agreements').fetchone()[0],'fulfilled')
    def test_agreement_revision_does_not_rebuild_journal_dependency(self):
        before=self.store.reflection.context_dependency([],self.scope)
        row=self.propose();self.accept(row)
        self.assertEqual(before,self.store.reflection.context_dependency([],self.scope))
    def test_receipt_and_fulfillment_commit_atomically(self):
        row=self.accept(self.propose())
        handoff=self.store.pipeline.propose_handoff(self.work,self.identity,{'objective':'提醒','delaySeconds':0,'agreement':{'id':row['id'],'revision':2}})
        self.db.execute("INSERT INTO pipeline_actions VALUES('action','fp',1,?,0,'working',NULL,?)",(self.scope,time.time()))
        self.db.execute("CREATE TRIGGER fail_fulfilled BEFORE UPDATE OF status ON memory_agreements WHEN new.status='fulfilled' BEGIN SELECT RAISE(ABORT,'injected failure'); END");self.db.commit()
        work=NS(event_id='action');result={'status':'completed','effects':[{'status':'confirmed'}],'unfinished':[]}
        with self.assertRaises(sqlite3.IntegrityError):self.store.pipeline.finish_internal(work,result,handoff['requestId'])
        self.assertEqual(self.db.execute('SELECT state FROM pipeline_actions').fetchone()[0],'working')
        self.assertEqual(self.db.execute('SELECT status FROM memory_agreements').fetchone()[0],'accepted')
        self.db.execute('DROP TRIGGER fail_fulfilled');self.db.commit()
        self.store.pipeline.finish_internal(work,result,handoff['requestId'])
        self.assertEqual(self.db.execute('SELECT state FROM pipeline_actions').fetchone()[0],'completed')
        self.assertEqual(self.db.execute('SELECT status FROM memory_agreements').fetchone()[0],'fulfilled')
    def test_ddl_does_not_commit_caller_transaction(self):
        self.db.execute('CREATE TABLE sentinel(value TEXT)');self.db.commit()
        self.db.execute("INSERT INTO sentinel VALUES('uncommitted')")
        agreements.initialize(self.db);self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM sentinel').fetchone()[0],0)
    def test_model_research_reuses_handoff_without_user_request_origin(self):
        self.work.source='scheduled_contact'
        with self.assertRaises(ValueError):self.store.pipeline.propose_handoff(self.work,self.identity,{'objective':'发消息','delaySeconds':0,'kind':'message'})
        result=self.store.pipeline.propose_handoff(self.work,self.identity,{'objective':'研究真实兴趣','delaySeconds':0,'kind':'research'})
        data=json.loads(self.db.execute('SELECT data FROM pipeline_handoffs WHERE id=?',(result['requestId'],)).fetchone()[0])
        self.assertEqual(data['origin'],'model');self.assertEqual(data['kind'],'research')

class Freshness(unittest.IsolatedAsyncioTestCase):
    async def test_tool_reply_counts_only_confirmed_same_scope_delivery(self):
        from unittest.mock import AsyncMock,patch
        import group_actions
        for target,ack in [('87654321',{'message_id':42}),('55555555',{'message_id':43}),('87654321',{})]:
            with tempfile.TemporaryDirectory() as folder:
                store=BehaviorStore(Path(folder)/'behavior.sqlite')
                work=Work('bot','group:87654321','12345678','90000000','event','message',time.time(),{})
                work.stage('ingress')
                extra={'_hosted_pipeline':work}
                event=NS(get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}),get_sender_id=lambda:'12345678',get_self_id=lambda:'90000000',get_group_id=lambda:'87654321',message_obj=NS(message_id='event'),bot=NS(send_group_msg=AsyncMock(return_value=ack)))
                cfg={'groups':[],'behavior':{'enabled':True,'ownerUin':'12345678'}}
                with patch.object(group_actions,'group_access',AsyncMock()):
                    await group_actions.send(event,cfg,store.db,target,'实际回复','',lambda:True,lambda *args:None)
                store.pipeline.finish(work,{})
                same=target=='87654321'
                self.assertEqual(work.planned_messages,int(same))
                self.assertEqual(work.confirmed_messages,int(same and bool(ack)))
                if same:self.assertEqual(work.status,'sent' if ack else 'unknown')
                store.close()

    async def test_own_receipt_and_other_scope_never_trigger_paid_review(self):
        from group_freshness import GroupFreshness
        f=GroupFreshness();now=time.monotonic()
        f.observe('private:12345678','e1','12345678','原话',now=now)
        cursor=f.cursor('private:12345678')
        f.observe('private:12345678','receipt','bot','第一气泡',role='self',now=now+1)
        f.observe('group:87654321','e2','22222222','其他会话',now=now+1)
        async def forbidden(_):raise AssertionError('no new target message')
        result=await f.check('private:12345678',cursor,now,'e1','第二气泡','第一气泡',forbidden,lambda:True)
        self.assertFalse(result['checked']);self.assertEqual(result['action'],'keep')

if __name__=='__main__':unittest.main()
