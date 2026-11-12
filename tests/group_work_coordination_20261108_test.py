import asyncio,json,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from group_freshness import GroupFreshness
from message_provenance import put,responded_by,settle_response
from bot_pipeline import conversation_context,decision_conversation


class Coordination(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite')
        self.scope='group:55555';self.subject='12345678'
    def tearDown(self):self.store.close();self.temp.cleanup()

    async def test_same_scope_waits_for_settlement_other_scope_keeps_running(self):
        s=self.store;self.assertTrue(await s.acquire_scope(self.scope,lambda:True));entered=asyncio.Event()
        async def next_work():
            self.assertTrue(await s.acquire_scope(self.scope,lambda:True));entered.set();s.release_scope(self.scope)
        waiter=asyncio.create_task(next_work());await asyncio.sleep(0)
        self.assertFalse(entered.is_set());self.assertTrue(await s.acquire_scope('private:12345678',lambda:True))
        s.release_scope('private:12345678');s.release_scope(self.scope)
        await asyncio.wait_for(waiter,1);self.assertEqual(s.scope_turns,{})

    async def test_cancel_timeout_and_invalid_owner_do_not_leak_or_unlock_current_owner(self):
        s=self.store;await s.acquire_scope(self.scope,lambda:True)
        waiter=asyncio.create_task(s.acquire_scope(self.scope,lambda:True));await asyncio.sleep(0);waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiter
        with self.assertRaises(asyncio.TimeoutError):await s.acquire_scope(self.scope,lambda:True,timeout=.001)
        self.assertEqual(s.scope_turns[self.scope]['users'],1)
        self.assertTrue(s.scope_turns[self.scope]['lock'].locked());s.release_scope(self.scope)
        self.assertFalse(await s.acquire_scope(self.scope,lambda:False));self.assertEqual(s.scope_turns,{})
        def broken():raise RuntimeError('configuration gone')
        with self.assertRaises(RuntimeError):await s.acquire_scope(self.scope,broken)
        self.assertEqual(s.scope_turns,{})

    def test_confirmed_reply_after_ingress_visible_at_actual_assembly_with_real_recipient(self):
        s=self.store;ingress=time.time()-10
        s.reflection.incoming(self.scope,self.subject,'original','甲的问题',ingress)
        s.participation.confirmed(self.scope,'给甲的真实回复',ingress+5,self.subject,'original',
                                  {'sender_id':'99999999','receipt':'confirmed','message_ids':['qq-ack'],'complete':False})
        cfg={'memory':True,'memorySummary':False}
        old=conversation_context(s,cfg,self.scope,'87654321','乙的问题',ingress)
        new=conversation_context(s,cfg,self.scope,'87654321','乙的问题',time.time())
        self.assertFalse(any(x['role']=='self' for x in old['conversation_timeline']))
        sent=next(x for x in new['conversation_timeline'] if x['role']=='self')
        self.assertEqual(sent['recipient_id'],self.subject);self.assertEqual(sent['message_ids'],['qq-ack'])
        self.assertEqual(decision_conversation(s,cfg,self.scope,'87654321',time.time())['timeline'],new['conversation_timeline'])
        self.assertFalse(decision_conversation(s,cfg,'group:66666',self.subject,time.time())['timeline'])

    async def test_only_explicit_observed_coverage_with_complete_work_ack_can_settle_next_event(self):
        f=GroupFreshness();f.observe(self.scope,'A',self.subject,'原问题');cursor=f.cursor(self.scope);started=time.monotonic()
        f.observe(self.scope,'B',self.subject,'补充');f.observe(self.scope,'C','87654321','独立问题')
        async def review(payload):return json.dumps({'action':'replace','text':'原问题和补充的答复','responded_events':['B']})
        result=await f.check(self.scope,cursor,started,'A','原草稿','',review,lambda:True)
        self.assertEqual(result['responded_events'],['B'])
        db=self.store.db;proof={'receipt':'confirmed','message_ids':['qq-ack'],'complete':False,'responded_events':result['responded_events']}
        put(db,self.scope,'A','outgoing',proof);db.commit()
        self.assertIsNone(responded_by(db,self.scope,'B'),'first bubble is not whole Work completion')
        settle_response(db,self.scope,'A',False);self.assertIsNone(responded_by(db,self.scope,'B'))
        settle_response(db,self.scope,'A',True);self.assertEqual(responded_by(db,self.scope,'B'),'A')
        self.assertIsNone(responded_by(db,self.scope,'C'));self.assertIsNone(responded_by(db,'group:66666','B'))
        self.store.close();self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite')
        self.assertEqual(responded_by(self.store.db,self.scope,'B'),'A','explicit coverage survives restart')
        for status in ('unknown','failed'):
            put(self.store.db,self.scope,'A','outgoing',{**proof,'receipt':status,'complete':True});self.store.db.commit()
            self.assertIsNone(responded_by(self.store.db,self.scope,'B'))

    async def test_reviewer_cannot_cover_unseen_event_or_partial_reply(self):
        f=GroupFreshness();f.observe(self.scope,'A',self.subject,'原问题');cursor=f.cursor(self.scope);started=time.monotonic()
        f.observe(self.scope,'B',self.subject,'新消息')
        async def forged(payload):return json.dumps({'action':'keep','responded_events':['not-observed']})
        self.assertNotIn('responded_events',await f.check(self.scope,cursor,started,'A','草稿','',forged,lambda:True))
        async def partial(payload):return json.dumps({'action':'keep','responded_events':['B']})
        self.assertEqual((await f.check(self.scope,cursor,started,'A','草稿','已经发过',partial,lambda:True))['responded_events'],[])

    def test_fatigue_shortens_existing_followup_window_and_recovers_without_model_input(self):
        p=self.store.participation;cfg={'followupSeconds':120,'fatigueWindowSeconds':300,'fatigueMaxReplies':4}
        self.assertEqual(p.followup_window(self.scope,cfg,1000),120)
        for at in (990,992,994,996):p.outgoing(self.scope,'真实发言',at,subject=self.subject)
        self.assertLess(p.followup_window(self.scope,cfg,1000),40)
        self.assertEqual(p.followup_window(self.scope,cfg,1300),120)
        self.assertEqual(p.followup_window(self.scope,{**cfg,'fatigueEnabled':False},1000),120)
        self.assertIsNone(p.blocked_reason(self.scope,'@真实邀请',cfg,directed=True,now=1000))
        self.assertNotIn('rhythm',p.context(self.scope,now=1000,cfg=cfg))

    def test_multi_author_claims_follow_each_event_not_work_trigger(self):
        from summary_material import derive_claims
        rows=[{'event':'A','sender_id':self.subject,'user':'我下个月要去杭州工作','source':{'sender_id':self.subject}},
              {'event':'B','sender_id':'87654321','user':'我下个月要去苏州工作','source':{'sender_id':'87654321'}}]
        selected={'evidence':[{'event':r['event'],'who':'user','quote':r['user']} for r in rows]}
        claims=derive_claims(self.scope,rows,{},[],selected)
        self.assertEqual([c['speaker']['id'] for c in claims],[self.subject,'87654321'])
        rows[1]['sender_id']=self.subject
        claims=derive_claims(self.scope,rows,{},[],selected)
        self.assertEqual(len(claims),1,'conflicting protocol author is not relabeled as the Work trigger')

    def test_consumed_aborted_checkpoint_keeps_old_wire_appendable_without_reissuing_permit(self):
        from unittest.mock import patch
        from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
        from context_protocol import SOURCE,READONLY_CURRENT,model_json
        db=self.store.db;reflection=self.store.reflection;scope='private:'+self.subject
        user=lambda ident:{'role':'user','content':SOURCE+model_json({'scope':scope,'sender_id':self.subject,'event_id':ident})+'\nreal question'}
        from context_transport import EVENT_CONTEXT_CURRENT
        packet=[{'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'identity':{'sender_id':self.subject}},'policy':'fixed','currentGuidance':'current','meaning':'platform event data'})},
                {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}]
        base=[{'role':'system','content':'immutable system'},user('first')]
        j=ReadonlyJournal(db,scope);initial_stamp=reflection.reconstruction_stamp(scope)
        kw={'strict':True,'reconstruction_authority':reflection,'reconstruction_scope':scope}
        p=j.prepare(base+packet,1,retirement=initial_stamp,**kw);self.assertTrue(j.commit(p));old=p.wire
        # A real checkpoint's lifecycle is represented by this fixture. No
        # live round/permit is minted or restored by the recovery path.
        db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,'fixture summary','[]',?)",(scope,time.time()))
        summary=db.execute('SELECT max(id) FROM memory_summaries').fetchone()[0]
        db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id) VALUES(?,35,35,0,'available',?,?)",(scope,time.time(),summary));db.commit()
        current_stamp=reflection.reconstruction_stamp(scope)
        with patch('system_contract.request_identity',return_value='fixture-staged-request'):
            staged=j.prepare(base+[{'role':'assistant','content':'real answer'},user('aborted')]+packet,2,retirement=current_stamp,**kw)
            self.assertTrue(j.stage_candidate(staged));self.assertTrue(j.abort_candidate(staged))
        self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
        self.assertEqual(j.last.wire,old)
        new_base=base+[{'role':'assistant','content':'real answer'},user('next')]
        j=ReadonlyJournal(db,scope)  # A restart must not require in-memory stamps.
        with patch('system_contract.request_identity',return_value='fixture-next-request'):
            continued=j.prepare(new_base+packet,3,retirement=current_stamp,**kw)
        self.assertIsNone(continued.reconstruction_permit);self.assertEqual(continued.retirement,initial_stamp)
        self.assertEqual(continued.wire[:len(old)],old);self.assertTrue(j.commit(continued))
        self.assertEqual(db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
        self.assertFalse(reflection.can_defer_context_retirement('group:55555',initial_stamp))
        db.execute('UPDATE memory_reflection_control SET epoch=1');db.commit()
        with self.assertRaises(ContextReconstructionDenied):
            j.prepare(new_base+[{'role':'assistant','content':'next answer'},user('after-forget')]+packet,4,retirement=reflection.reconstruction_stamp(scope),**kw)

    def test_system_preflight_does_not_spend_or_invent_an_independent_system_permit(self):
        c=self.store.system_contract;old=[{'role':'system','content':'committed'}];new=[{'role':'system','content':'new configuration'}]
        c.reserve(old,None,'initial',time.time(),scope=self.scope)
        self.assertFalse(c.transition_available(new,None,'next',scope=self.scope))
        self.assertTrue(c.transition_available(new,'trusted-config','next',scope=self.scope))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM fishbot_system_permits').fetchone()[0],0,'preflight is read-only')
        r=c.reserve(new,'trusted-config','next',time.time(),scope=self.scope);self.assertTrue(c.settle(r,True,time.time()))
        self.assertFalse(c.transition_available(old,'trusted-config','later',scope=self.scope))
        self.assertFalse(c.transition_available(new,'trusted-config','next',scope='group:66666'))


    def test_service_group_transition_restores_and_requires_previous_real_ack(self):
        from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
        from context_protocol import SOURCE,READONLY_CURRENT,model_json
        scope=self.scope;who=self.subject;db=self.store.db
        system={'role':'system','content':'unchanged'}
        def user(event):return {'role':'user','content':SOURCE+model_json({'scope':scope,'event_id':event,'sender_id':who})+'\n真实消息'}
        def packet(state):return {'role':'user','content':READONLY_CURRENT+'\n'+model_json(state)}
        j=ReadonlyJournal(db,scope)
        a=j.prepare([system,user('human'),packet({})],1,group_turn=(scope,'human',who));self.assertTrue(j.commit(a))
        state={'group_round_history':[{'source_scope':scope,'round_event':'human','messages':[{'source_scope':scope,'source_event':'human','sender_id':who}],'reply':{'role':'self','recipient_id':who,'text':'实际回复'}}]}
        internal={'role':'user','content':'[平台内部主动时刻]'}
        db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed) VALUES(?,?,?,?,'scheduled_contact','context','received','',0,0)",(scope,who,'timer',time.time()));db.commit()
        b=j.prepare([system,internal,packet(state)],2,group_turn=(scope,'timer',who))
        self.assertEqual(b.wire[:len(a.wire)],a.wire);self.assertTrue(j.commit(b))
        j=ReadonlyJournal(db,scope);self.assertFalse(j.recovery_error);self.assertEqual(j.last.group_turn,(scope,'timer',who))
        with self.assertRaises(ContextReconstructionDenied):j.prepare([system,user('next'),packet(state)],3,group_turn=(scope,'next',who))
        put(db,scope,'timer','outgoing',{'receipt':'confirmed','message_ids':['ack'],'complete':True});db.commit()
        c=j.prepare([system,user('next'),packet(state)],3,group_turn=(scope,'next',who))
        self.assertEqual(c.wire[:len(b.wire)],b.wire)
        with self.assertRaises(ContextReconstructionDenied):j.prepare([system,user('other'),packet(state)],4,group_turn=('group:66666','other',who))

    def test_absolute_schedule_is_idempotent_and_projects_existing_protocol(self):
        from bot_pipeline import Work
        from unittest.mock import patch
        identity={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'epoch':1}
        scope='private:'+self.subject
        work=Work('bot',scope,self.subject,'99999999','clock-request','message',time.time(),identity,ledger_owner=True)
        now=int(time.time()*1000);due=now+120000
        request={'objective':'按明确钟点提醒','delaySeconds':0,'kind':'message','runAt':due}
        with patch('bot_pipeline.time.time',return_value=now/1000):
            first=self.store.pipeline.propose_handoff(work,identity,request)
        with patch('bot_pipeline.time.time',return_value=(now+5000)/1000):
            again=self.store.pipeline.propose_handoff(work,identity,request)
        self.assertEqual(first,again)
        projected=self.store.pipeline.pending_handoffs(1)[0]
        self.assertNotIn('runAt',projected)
        self.assertEqual(projected['at']+projected['delaySeconds']*1000,due)
        with self.assertRaisesRegex(ValueError,'identity_conflict'):
            self.store.pipeline.propose_handoff(work,identity,{**request,'runAt':due+1000})
        for when in (now-1,now+604800001,True):
            bad=Work('bot',scope,self.subject,'99999999',str(when),'message',time.time(),identity,ledger_owner=True)
            with patch('bot_pipeline.time.time',return_value=now/1000),self.assertRaisesRegex(ValueError,'invalid_run_at'):
                self.store.pipeline.propose_handoff(bad,identity,{**request,'runAt':when})

    def test_model_explicit_acceptance_and_handoff_are_atomic_scoped_and_idempotent(self):
        from bot_pipeline import Work
        import agreement_state
        db=self.store.db;identity={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'epoch':1}
        scope='private:'+self.subject
        work=Work('bot',scope,self.subject,'99999999','request','message',time.time(),identity,ledger_owner=True)
        row,error=agreement_state.apply_update(db,'propose','今天18点提醒',self.subject,[self.subject,'bot'],scope,'request',due='今天18:00');db.commit()
        self.assertFalse(error)
        request={'objective':'按用户要求到点提醒','delaySeconds':600,'kind':'message','agreement':{'id':row['id'],'revision':1},'acceptAgreement':True}
        result=self.store.pipeline.propose_handoff(work,identity,request)
        self.assertEqual(result['state'],'pending')
        projected=self.store.pipeline.pending_handoffs(1)[0]
        self.assertNotIn('acceptAgreement',projected)
        self.assertEqual(projected['agreement'],{'id':row['id'],'revision':2})
        state=db.execute('SELECT status,revision,handoff_id,task_revision FROM memory_agreements WHERE id=?',(row['id'],)).fetchone()
        self.assertEqual(state,('accepted',2,result['requestId'],2))
        self.assertEqual(self.store.pipeline.propose_handoff(work,identity,request)['requestId'],result['requestId'])
        self.assertEqual(db.execute('SELECT count(*) FROM pipeline_handoffs').fetchone()[0],1)
        with self.assertRaises(ValueError):self.store.pipeline.propose_handoff(work,identity,{**request,'delaySeconds':601})
        agreement_state.apply_update(db,'cancel','今天18点提醒',self.subject,[self.subject,'bot'],scope,'request',expected_revision=2);db.commit()
        self.assertFalse(agreement_state.task_current(db,{'id':row['id'],'revision':2}))
        # Failed queue admission rolls back the preceding model acceptance.
        other=Work('bot',scope,self.subject,'99999999','other','message',time.time(),identity,ledger_owner=True)
        row,error=agreement_state.apply_update(db,'propose','明天提醒',self.subject,[self.subject,'bot'],scope,'other');db.commit()
        for n in range(16):db.execute("INSERT INTO pipeline_handoffs VALUES(?,1,'{}','pending',NULL,?)",('full'+str(n),time.time()))
        db.commit()
        with self.assertRaisesRegex(ValueError,'queue_full'):
            self.store.pipeline.propose_handoff(other,identity,{**request,'agreement':{'id':row['id'],'revision':1}})
        self.assertEqual(db.execute('SELECT status,revision,handoff_id FROM memory_agreements WHERE id=?',(row['id'],)).fetchone(),('proposed',1,None))


if __name__=='__main__':unittest.main()
