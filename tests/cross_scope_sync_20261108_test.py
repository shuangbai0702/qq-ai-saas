"""Same-bot internal continuity; never rewrite submitted wire or grant authority."""
import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore,is_no_reply_marker
from bot_pipeline import conversation_context,decision_memory,decision_version,annotate_speaker,Work,CROSS_SCOPE_GUIDANCE
from memory_scope import pending_messages,summary_rows,retained_context_visible,core_note_visible
from message_provenance import put
from readonly_journal import ReadonlyJournal,ContextReconstructionDenied
from context_protocol import READONLY_CURRENT,model_json
from context_transport import READONLY_FIELDS,EVENT_CONTEXT_CURRENT
import agreement_state as ag


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'bot.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.now=time.time()
        self.owner='12345678';self.peer='87654321';self.group='group:55555';self.other='group:66666'
        self.private='private:'+self.owner;self.cfg={'ownerUin':self.owner,'memory':True,'memorySummary':True}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def incoming(self,scope,person,event,text,at=None,proof=True):
        self.store.reflection.incoming(scope,person,event,text,at or self.now,
            source={'sender_id':person,'sender_name':'fixture','mentions':[]} if proof else None)
    def context(self,scope=None,person=None):
        return conversation_context(self.store,self.cfg,scope or self.group,person or self.owner,'question',self.now+1)
    def propose(self,n=0,audiences=None):
        row,error=ag.apply_update(self.db,'propose','静默要求'+str(n),self.owner,[self.owner,'bot'],self.private,str(n),
             audiences or [self.group],detail='群里包括被@也不回复，直到主人取消',cfg={**self.cfg,'groups':['55555','66666']})
        self.assertFalse(error)
        row,error=ag.apply_update(self.db,'accept','静默要求'+str(n),'bot',[self.owner,'bot'],self.private,str(n),expected_revision=1)
        self.assertFalse(error);self.db.commit();return row
    def test_three_directions_before_any_completed_round(self):
        self.incoming(self.private,self.owner,'p','private question')
        self.incoming(self.group,self.owner,'g','group question')
        for target,events in [(self.group,{'p'}),(self.private,{'g'}),(self.other,{'p','g'})]:
            self.assertEqual({r['source_event'] for r in self.context(target)['recent_cross_session_messages']},events)
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
    def test_owner_tag_is_reference_not_new_command(self):
        self.incoming(self.private,self.owner,'p','owner message')
        row=self.context()['recent_cross_session_messages'][0]
        self.assertEqual(row['attention'],'[important]');self.assertEqual(row['sender_id'],self.owner)
        self.assertIn('旧话不是新的主人指令',CROSS_SCOPE_GUIDANCE)
        self.assertNotIn('source',row);self.assertNotIn('age_seconds',row)
    def test_unverified_old_aggregate_not_relabelled_owner(self):
        self.incoming(self.private,self.owner,'old','甲说A，乙说B',proof=False)
        row=self.context()['recent_cross_session_messages'][0]
        self.assertEqual(row['attribution'],'legacy_author_unverified');self.assertNotIn('attention',row)
    def test_multi_author_work_and_references(self):
        self.incoming(self.private,self.owner,'owner','owner requirement')
        self.incoming('private:'+self.peer,self.peer,'peer','peer detail')
        self.incoming(self.group,self.peer,'current','actual peer question')
        rows=self.context(person=self.peer)['recent_cross_session_messages']
        self.assertEqual({r['sender_id'] for r in rows},{self.owner,self.peer})
        self.assertEqual([r['attention'] for r in rows if r['sender_id']==self.owner],['[important]'])
    def test_other_person_and_bot_isolation(self):
        self.incoming('private:'+self.peer,self.peer,'peer','unrelated')
        self.assertEqual(self.context()['recent_cross_session_messages'],[])
        other=BehaviorStore(Path(self.tmp.name)/'other.sqlite')
        try:self.assertEqual(conversation_context(other,self.cfg,self.group,self.owner,'q',self.now+1)['recent_cross_session_messages'],[])
        finally:other.close()
    def test_bounded_and_fair_under_flood(self):
        self.incoming(self.private,self.owner,'p','owner requirement',self.now-1)
        for i in range(60):self.incoming(self.other,self.owner,str(i),'x'*800,self.now+i/1000)
        rows=pending_messages(self.db,self.group,self.owner,self.now+1,owner=self.owner)
        self.assertIn('p',{r['source_event'] for r in rows});self.assertLessEqual(len(rows),16)
        self.assertLessEqual(sum(len(model_json(r)) for r in rows),3000)
        self.assertTrue(any(r.get('excerpt') for r in rows))
    def test_future_and_invalid_private_sources_excluded(self):
        self.incoming(self.private,self.owner,'future','later',self.now+100)
        self.incoming('private:'+self.peer,self.owner,'wrong','misattributed')
        self.assertEqual(self.context()['recent_cross_session_messages'],[])
    def test_actual_own_reply_instead_of_pending_answer(self):
        self.incoming(self.private,self.owner,'p','hello')
        self.db.execute('UPDATE memory_pending SET answer=?',('unconfirmed draft',))
        self.db.execute('INSERT INTO dialogue_feedback(scope,event,subject,at,reply) VALUES(?,?,?,?,?)',(self.private,'send',self.owner,self.now,'actually sent'))
        put(self.db,self.private,'send','outgoing',{'receipt':'confirmed','message_ids':['qq1']});self.db.commit()
        rows=self.context()['recent_cross_session_messages']
        self.assertNotIn('unconfirmed draft',model_json(rows))
        self.assertEqual([r['text'] for r in rows if r['role']=='self'],['actually sent'])
    def test_unknown_send_not_projected_as_confirmed(self):
        self.incoming(self.private,self.owner,'p','hello')
        self.db.execute('INSERT INTO dialogue_feedback(scope,event,subject,at,reply) VALUES(?,?,?,?,?)',(self.private,'send',self.owner,self.now,'unknown side effect'))
        put(self.db,self.private,'send','outgoing',{'receipt':'unknown','message_ids':[]});self.db.commit()
        self.assertFalse(any(r['role']=='self' for r in self.context()['recent_cross_session_messages']))
    def test_facts_shared_correction_and_forget_preserved(self):
        self.store.observe(self.private,self.owner,'记住：饮料=绿茶','f',self.now)
        self.assertEqual(self.context()['memory_foundation']['facts'][0]['value'],'绿茶')
        self.store.observe(self.other,self.owner,'更正记忆：饮料=红茶','c',self.now+1)
        self.assertEqual(self.context()['memory_foundation']['facts'][0]['value'],'红茶')
        self.store.observe(self.private,self.owner,'忘记：饮料','x',self.now+2)
        self.assertEqual(self.context()['memory_foundation']['facts'],[])
        self.assertNotIn('facts',self.context()['state'])
    def test_summaries_keep_real_participation(self):
        a=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.private,'private fixture','[]',self.now)).lastrowid
        b=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(self.other,'group fixture','[]',self.now)).lastrowid
        self.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(b,self.owner));self.db.commit()
        self.assertEqual({r[0] for r in summary_rows(self.db,self.group,self.owner)},{a,b})
        self.assertEqual(summary_rows(self.db,self.group,self.peer),[])
        self.assertTrue(core_note_visible([self.private,self.other],self.group))
        self.assertFalse(core_note_visible(['unknown'],self.group))
    def test_agreement_detail_background_and_cross_scope_cancel(self):
        row=self.propose()
        active=ag.context_view(self.db,self.group,owner=self.owner)[0]
        self.assertTrue(active['applies_here']);self.assertIn('被@',active['detail'])
        self.assertFalse(ag.context_view(self.db,self.other,owner=self.owner)[0]['applies_here'])
        before=decision_version(self.db,self.group)
        self.assertIsNotNone(ag.find_open(self.db,self.other,'静默要求0',self.owner))
        _,error=ag.apply_update(self.db,'cancel','静默要求0',self.owner,[self.owner,'bot'],self.private,'0',expected_revision=2)
        self.assertFalse(error);self.db.commit();self.assertNotEqual(before,decision_version(self.db,self.group))
        self.assertEqual(ag.context_view(self.db,self.group,owner=self.owner)[0]['status'],'canceled')
    def test_live_agreements_not_hidden_by_eight_row_limit(self):
        for n in range(12):self.propose(n)
        rows=ag.context_view(self.db,self.group,owner=self.owner)
        self.assertEqual(len(rows),12);self.assertTrue(all(r['status']=='accepted' for r in rows))
    def test_owner_pair_enforced_and_legacy_not_promoted(self):
        _,error=ag.apply_update(self.db,'propose','test agreement',self.peer,[self.peer,'bot'],'private:'+self.peer,'p',cfg=self.cfg)
        self.assertEqual(error,'owner_agreement_required')
        row,error=ag.apply_update(self.db,'propose','legacy agreement',self.peer,[self.peer,'bot'],'private:'+self.peer,'old',[self.group])
        self.assertFalse(error);self.assertEqual(ag.context_view(self.db,self.group,owner=self.owner),[])
    def test_auxiliary_projection_and_memory_off(self):
        self.incoming(self.private,self.owner,'p','recent requirement')
        view=decision_memory(self.db,self.cfg,self.group,self.owner,self.now+1)
        self.assertEqual(view['recent_cross_session_messages'][0]['attention'],'[important]')
        self.assertEqual(decision_memory(self.db,{'memory':False},self.group,self.owner,self.now+1),{})
        self.assertEqual(conversation_context(self.store,{'memory':False},self.group,self.owner,'q',self.now+1)['recent_cross_session_messages'],[])
    def test_current_owner_attention_and_no_reply_unchanged(self):
        work=Work('bot',self.group,self.owner,'99999999','new','message',self.now,{})
        req=NS(prompt='hello');annotate_speaker(req,work,self.owner)
        self.assertIn('[important]',req.prompt);self.assertTrue(is_no_reply_marker('[NO_REPLY]'))
        self.assertFalse(is_no_reply_marker('a [NO_REPLY] reply'))
    def test_restart_preserves_pool(self):
        self.incoming(self.private,self.owner,'p','persisted')
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        self.assertEqual(self.context()['recent_cross_session_messages'][0]['text'],'persisted')
    def test_interest_aggregate_is_not_a_memory_source_envelope(self):
        wire=[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'companion':{'interests':[{'source_scope':'*','topic':'tea'}]}})}]
        self.assertTrue(retained_context_visible(self.db,wire,self.group))
        wire=[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'recent_cross_session_messages':[{'kind':'recent_uncompressed_message','source_scope':'invalid'}]})}]
        self.assertFalse(retained_context_visible(self.db,wire,self.group))
    def test_old_wire_exact_prefix_across_updates_expiry_and_reload(self):
        self.incoming(self.private,self.owner,'p','first message '*20)
        base=[{'role':'system','content':'frozen persona'},{'role':'user','content':'real first'}]
        packet=lambda state:{'role':'user','content':READONLY_CURRENT+'\n'+model_json({k:v for k,v in state.items() if k in READONLY_FIELDS})}
        event_packet={'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{},'policy':'fixed','currentGuidance':'fixture','meaning':'platform fixture'})}
        j=ReadonlyJournal(self.db,self.group)
        initial=j.prepare(base+[event_packet,packet(self.context())],('tenant','bot',1),now=self.now,retained_validator=lambda wire:retained_context_visible(self.db,wire,self.group))
        self.assertTrue(j.commit(initial));prefix=copy.deepcopy(initial.wire)
        for n in range(3):
            self.incoming(self.private,self.owner,'next'+str(n),'new real message '+str(n),self.now+n/10)
            state=self.context()
            if n==2:state['recent_cross_session_messages']=[]
            next_base=base+[{'role':'assistant','content':'reply '+str(n)},{'role':'user','content':'next '+str(n)}]
            p=j.prepare(next_base+[event_packet,packet(state)],('tenant','bot',1),now=self.now+n+1,retained_validator=lambda wire:retained_context_visible(self.db,wire,self.group))
            self.assertEqual(p.wire[:len(prefix)],prefix);self.assertEqual(p.wire[0],base[0]);self.assertIsNone(p.reconstruction_permit)
            self.assertEqual(model_json(p.wire).count('first message '*20),1,'unchanged source body must not be appended again')
            self.assertTrue(j.commit(p));prefix=copy.deepcopy(p.wire);base=next_base
            j=ReadonlyJournal(self.db,self.group)
        invalid=copy.deepcopy(base);invalid[1]['content']='rewritten history'
        with self.assertRaises(ContextReconstructionDenied):j.prepare(invalid+[event_packet,packet(state)],('tenant','bot',1),strict=True)


class AgreementTools(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_registration_acceptance_and_current_author_evidence(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'references/astrbot'))
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        with closing(BehaviorStore(Path(temp.name)/'bot.sqlite')) as store:
            owner='12345678';scope='private:'+owner;extras={}
            work=Work('bot',scope,owner,'99999999','real','message',time.time(),{})
            extras['_hosted_pipeline']=work
            text='在群里包括被@都先别回复'
            event=NS(get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.update({k:v}),get_message_str=lambda:text)
            store.reflection.incoming(scope,owner,'real',text,source={'sender_id':owner})
            settings={'config':{'behavior':{'ownerUin':owner},'groups':['55555']}}
            context=NS(context=NS(event=event))
            tool=ag.tools(settings,event,store.db,scope,lambda:True)[0]
            result=json.loads(await tool.call(context,action='propose',topic='群聊静默',detail=text,quote=text,audiences=['group:55555']))
            self.assertTrue(result['ok']);self.assertEqual(result['status'],'proposed')
            identifier=result['id']
            missing=json.loads(await tool.call(context,action='accept',topic='群聊静默',agreement_id='0'*24,speakerRole='bot',revision=1))
            self.assertEqual(missing['error'],'agreement_not_found')
            wrong_topic=json.loads(await tool.call(context,action='accept',topic='其他主题',agreement_id=identifier,speakerRole='bot',revision=1))
            self.assertEqual(wrong_topic['error'],'agreement_not_found')
            malformed=json.loads(await tool.call(context,action='accept',topic='群聊静默',agreement_id=1,speakerRole='bot',revision=1))
            self.assertEqual(malformed['error'],'invalid_agreement_id')
            result=json.loads(await tool.call(context,action='accept',topic='群聊静默',agreement_id=identifier,speakerRole='bot',revision=1))
            self.assertTrue(result['ok']);self.assertEqual(result['status'],'accepted')
            stale=json.loads(await tool.call(context,action='cancel',topic='群聊静默',agreement_id=identifier,speakerRole='bot',revision=1))
            self.assertEqual(stale['error'],'agreement_revision_required')
            row=ag.context_view(store.db,'group:55555',owner=owner)[0]
            self.assertTrue(row['applies_here']);self.assertEqual(row['detail'],text)
            # Quoted/merged words in the visible Work are not another author's assent.
            store.db.execute('UPDATE memory_pending SET subject=?',('87654321',));store.db.commit()
            bad=json.loads(await tool.call(context,action='cancel',topic='群聊静默',quote=text,revision=2))
            self.assertEqual(bad['error'],'current_user_evidence_required')
            extras['_hosted_pipeline']=Work('bot',scope,'87654321','99999999','other','message',time.time(),{})
            member_tool=ag.tools(settings,event,store.db,scope,lambda:True)[0]
            self.assertEqual(member_tool.parameters,tool.parameters,'catalog remains stable across speakers')
            denied=json.loads(await member_tool.call(context,action='cancel',topic='群聊静默',quote=text,revision=2))
            self.assertEqual(denied['error'],'owner_agreement_required')
            self.assertEqual(ag.context_view(store.db,'group:55555',owner=owner)[0]['status'],'accepted')


if __name__=='__main__':unittest.main(verbosity=2)
