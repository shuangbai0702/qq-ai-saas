"""Request projection, retirement and append-only evidence; no live accounts."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from decision_context import compact_shared,expand_shared,split_snapshot,expand_snapshot,restore_append,wire_contexts
from decision_history import SnapshotHistory,AppendHistory,MAX_TURNS,MAX_ADDED_CHARS


class DecisionContextTest(unittest.TestCase):
    def test_append_reset_diagnostics_match_actual_lifecycle(self):
        for reason,system,scope,stamp,now in [('system_changed','new',1,'s1',2),('binding_changed','a',2,'s1',2),('summary_changed','a',1,'s2',2),('idle_boundary','a',1,'s1',4000),('', 'a',1,'s1',2)]:
            h=AppendHistory();first=h.prepare('a',json.dumps(self.state()),1,now=1,retirement='s1')
            self.assertEqual(first.reset_reason,'initial_or_cleared');h.commit(first,'{}')
            p=h.prepare(system,json.dumps(self.state()),scope,now=now,retirement=stamp)
            self.assertEqual(p.reset_reason,reason)
            self.assertEqual(p.turns,2 if not reason else 1)
    def test_native_prompt_parts_keep_exact_previous_wire_prefix(self):
        h=AppendHistory();state=self.state();first=h.prepare('authority',json.dumps(state),('tenant','bot'),now=1)
        wire=wire_contexts(first.contexts)+[{'role':'user','content':[{'type':'text','text':first.prompt}]}]
        self.assertEqual(restore_append(wire),state)
        h.commit(first,'{}');state['environment']['now']=13
        second=h.prepare('authority',json.dumps(state),('tenant','bot'),now=2)
        replay=wire_contexts(second.contexts)
        self.assertEqual(replay[:len(wire)],wire)
        self.assertEqual(second.contexts[-2]['content'],first.prompt,'wire projection mutated journal')
        self.assertEqual(wire_contexts([{'role':'user','content':'{"future":true}'}]),[{'role':'user','content':'{"future":true}'}])

    def state(self):
        note={'text':'a sourced core note','sources':['1']}
        message={'source_scope':'group:12345678','source_event':'1','sender_id':'12345678','user':'a real message','bot':'','at':10,'age_seconds':2}
        conversation={'identity':{'scope':'group:12345678','observed_subject':'12345678'},'persona_core_note':note,
                      'state':{'emotion':'happy','facts':[{'id':'fact','text':'a stable fact'}],'conversation_summaries':[{'id':1,'summary':'long background'}]},
                      'recent_messages':[message],'recent_cross_session_messages':[message],
                      'conversation_timeline':[],'group_round_history':[],'dialogue':{'current':True},'future':{'must':'remain'}}
        return {'environment':{'now':12,'contacts':[{'scope':'private:12345678','mayInitiate':True}],
                              'sharedContext':{'memoryEnabled':True,'conversations':[conversation,copy.deepcopy(conversation)],
                                               'autonomous_experience':{'actions':[{'id':'a','status':'unknown'}],'decisions':[]},
                                               'execution_experience':{'experiences':[{'id':'e','result':'partial','evidence':['real']} ]}}},
                'goals':[{'id':'g','status':'active'}],'feedback':{'status':'unknown'},'future':[{'context':'preserve'}]}

    def test_common_note_once_and_divergent_notes_not_merged(self):
        shared=self.state()['environment']['sharedContext'];before=copy.deepcopy(shared)
        projected=compact_shared(shared)
        self.assertEqual(expand_shared(projected),shared);self.assertEqual(shared,before)
        self.assertNotIn('persona_core_note',projected['conversations'][0])
        self.assertEqual(projected['persona_core_note'],shared['conversations'][0]['persona_core_note'])
        shared['conversations'][1]['persona_core_note']['text']='a different owned value'
        self.assertEqual(compact_shared(shared),shared)

    def test_exact_expansion_sources_receipts_unknown_fields_and_ages(self):
        state=self.state();before=copy.deepcopy(state);current,records=split_snapshot(state)
        self.assertEqual(expand_snapshot(current,records),state);self.assertEqual(state,before)
        self.assertEqual(current['feedback'],{'status':'unknown'})
        self.assertEqual(current['goals'],state['goals']);self.assertEqual(current['future'],state['future'])
        shared=current['environment']['sharedContext'];a,b=shared['conversations']
        self.assertEqual(a['recent_messages'][0]['contextRecordRef'],b['recent_cross_session_messages'][0]['contextRecordRef'])
        self.assertEqual(a['recent_messages'][0]['age_seconds'],2)
        self.assertNotIn('age_seconds',records[a['recent_messages'][0]['contextRecordRef']]['value'])
        self.assertEqual(shared['autonomous_experience']['actions'][0].keys(),{'contextRecordRef'})
        self.assertEqual(a['state']['emotion'],'happy')

    def test_reserved_literal_and_unknown_shapes_are_not_reinterpreted(self):
        for literal in ({'contextRecordRef':'not-a-generated-id'},{'contextRecordRef':[]},{'contextRecordRef':None}):
            state=self.state();state['future']=literal;current,records=split_snapshot(state)
            self.assertEqual(records,{});self.assertEqual(expand_snapshot(current,records),state)
        state={'environment':{'sharedContext':{'conversations':[None,{'state':None,'recent_messages':{'unknown':'shape'}}]}}}
        current,records=split_snapshot(state);self.assertEqual(expand_snapshot(current,records),state)

    def test_append_only_history_one_latest_snapshot_and_fresh_state(self):
        h=SnapshotHistory();old=self.state();p=h.prepare('authority',json.dumps(old),('tenant','bot',1),now=1)
        self.assertEqual(expand_snapshot(json.loads(p.prompt)['currentSnapshot'],p.records),old)
        h.commit(p,'{"decision":"wait"}');prefix=copy.deepcopy(h.history)
        new=copy.deepcopy(old);new['environment']['now']=13;new['environment']['contacts'][0]['mayInitiate']=False
        c=new['environment']['sharedContext']['conversations'][0];c['state']['emotion']='angry';c['recent_messages'][0]['age_seconds']=3
        c['recent_messages'].append({'source_scope':'group:12345678','source_event':'2','sender_id':'23456789','user':'new message','bot':'','at':13})
        new['environment']['sharedContext']['autonomous_experience']['actions'][0]['status']='confirmed'
        p2=h.prepare('authority',json.dumps(new),('tenant','bot',1),now=2)
        self.assertEqual(p2.contexts[:len(prefix)],prefix)
        self.assertEqual(expand_snapshot(json.loads(p2.prompt)['currentSnapshot'],p2.records),new)
        self.assertNotIn('currentSnapshot',json.dumps(p2.contexts));self.assertNotIn('stateChanges',json.dumps(p2.contexts))
        self.assertNotIn('happy',json.dumps(p2.contexts));self.assertNotIn('angry',json.dumps(p2.contexts))
        self.assertNotIn('mayInitiate',json.dumps(p2.contexts));self.assertFalse(json.loads(p2.prompt)['currentSnapshot']['environment']['contacts'][0]['mayInitiate'])
        self.assertNotIn('snapshot',h.__dict__)
        p2.contexts[0]['content']='provider request copy changed';self.assertEqual(h.history,prefix)

    def test_summary_epoch_tenant_system_memory_disable_reset(self):
        for kind in ('summary','epoch','tenant','system','disabled'):
            h=SnapshotHistory();old=self.state();h.commit(h.prepare('authority',json.dumps(old),('tenant','bot',1),now=1),'{}')
            new=self.state();scope=('tenant','bot',1);system='authority'
            if kind=='summary':new['environment']['sharedContext']['conversations'][0]['state']['conversation_summaries']=[]
            if kind=='epoch':scope=('tenant','bot',2)
            if kind=='tenant':scope=('other','bot',1)
            if kind=='system':system='new authority'
            if kind=='disabled':new['environment']['sharedContext']={'memoryEnabled':False,'conversations':[]}
            p=h.prepare(system,json.dumps(new),scope,now=2)
            self.assertEqual(set(p.records),set(split_snapshot(new)[1]));self.assertEqual(p.turns,1)

    def test_stale_failure_cancel_restart_and_turn_bounds(self):
        h=SnapshotHistory();state=self.state();a=h.prepare('a',json.dumps(state),1,now=1);b=h.prepare('a',json.dumps(state),2,now=2)
        self.assertFalse(h.commit(a,'{}'));self.assertTrue(h.commit(b,'{}'));h.discard(a);self.assertTrue(h.history)
        c=h.prepare('a',json.dumps(state),2,now=3);h.discard(c);self.assertFalse(h.commit(c,'{}'));self.assertEqual(h.records,{})
        for i in range(MAX_TURNS):h.commit(h.prepare('a',json.dumps(state),1,now=i+1),'{}')
        self.assertEqual(h.prepare('a',json.dumps(state),1,now=MAX_TURNS+1).turns,1)
        self.assertEqual(h.prepare('a',json.dumps(state),1,now=4000).turns,1)
        self.assertEqual(SnapshotHistory().prepare('a',json.dumps(state),1).turns,1)
        with self.assertRaises(ValueError):h.commit(h.prepare('a',json.dumps(state),1),'not json')

    def test_whole_request_append_sparse_runtime_and_no_unchanged_events(self):
        h=AppendHistory();state=self.state();state['autonomousActivity']={'fatigue':2,'energy':98,'meaning':'activity is not authorization'}
        first=h.prepare('authority',json.dumps(state),('tenant','bot',1),now=1)
        self.assertEqual(restore_append(first.contexts+[{'role':'user','content':first.prompt}]),state)
        self.assertTrue(h.commit(first,'{"decision":"wait"}'));prefix=copy.deepcopy(h.history)
        state['environment']['now']=13
        second=h.prepare('authority',json.dumps(state),('tenant','bot',1),now=2)
        self.assertEqual(second.contexts,prefix)
        self.assertEqual(restore_append(second.contexts+[{'role':'user','content':second.prompt}]),state)
        self.assertIn('runtimeUpdates',json.loads(second.prompt));self.assertEqual(list(json.loads(second.prompt)['runtimeUpdates'].values()),[13])
        self.assertLess(len(second.prompt),80)
        h.commit(second,'{"decision":"continue"}');prefix=copy.deepcopy(h.history)
        state['environment']['sharedContext']['conversations'][0]['recent_messages'].append({'source_scope':'group:12345678','source_event':'new','sender_id':'87654321','at':13,'user':'new true message','bot':''})
        state['goals'][0]['status']='completed';state['environment']['contacts'][0]['mayInitiate']=False
        third=h.prepare('authority',json.dumps(state),('tenant','bot',1),now=3)
        self.assertEqual(third.contexts[:len(prefix)],prefix)
        self.assertEqual(restore_append(third.contexts+[{'role':'user','content':third.prompt}]),state)
        added=third.contexts[len(prefix):]
        self.assertEqual(sum(len(json.loads(m['content']).get('contextRecords',[])) for m in added),1)
        self.assertIn('completed',json.dumps(added));h.commit(third,'{}');prefix=copy.deepcopy(h.history)
        fourth=h.prepare('authority',json.dumps(state),('tenant','bot',1),now=4)
        self.assertEqual(fourth.contexts,prefix)
        self.assertEqual(json.loads(fourth.prompt),{'runtimeUpdates':{}})

    def test_append_retirement_deleted_fields_and_late_commit(self):
        for kind in ('summary','epoch','tenant','system','disabled'):
            h=AppendHistory();state=self.state();h.commit(h.prepare('a',json.dumps(state),('tenant',1),now=1),'{}')
            changed=copy.deepcopy(state);scope=('tenant',1);system='a'
            if kind=='summary':changed['environment']['sharedContext']['conversations'][0]['state']['conversation_summaries']=[]
            if kind=='epoch':scope=('tenant',2)
            if kind=='tenant':scope=('other',1)
            if kind=='system':system='new authorization'
            if kind=='disabled':changed['environment']['sharedContext']={'memoryEnabled':False}
            p=h.prepare(system,json.dumps(changed),scope,now=2)
            self.assertEqual(p.turns,1);self.assertFalse(any(m['role']=='assistant' for m in p.contexts))
            self.assertEqual(restore_append(p.contexts+[{'role':'user','content':p.prompt}]),changed)
        h=AppendHistory();state=self.state();a=h.prepare('a',json.dumps(state),1);b=h.prepare('a',json.dumps(state),2)
        self.assertFalse(h.commit(a,'{}'));h.commit(b,'{}');h.discard(a);self.assertTrue(h.history)
        p=h.prepare('a',json.dumps(state),2);h.discard(p);self.assertFalse(h.commit(p,'{}'));self.assertIsNone(h.view)
        h=AppendHistory();h.commit(h.prepare('a',json.dumps(state),1),'{}');del state['future'];state['feedback']={'status':'confirmed','evidence':['actual']}
        p=h.prepare('a',json.dumps(state),1)
        self.assertEqual(restore_append(p.contexts+[{'role':'user','content':p.prompt}]),state)

    def test_recall_selection_is_not_summary_replacement(self):
        h=AppendHistory();state=self.state();p=h.prepare('a',json.dumps(state),1,now=1,retirement='stored summaries version 1');h.commit(p,'{}');prefix=copy.deepcopy(h.history)
        state['environment']['sharedContext']['conversations'][0]['state']['conversation_summaries']=[{'id':2,'summary':'another existing topic'}]
        p=h.prepare('a',json.dumps(state),1,now=2,retirement='stored summaries version 1')
        self.assertEqual(p.contexts[:len(prefix)],prefix);self.assertEqual(p.turns,2)
        self.assertEqual(restore_append(p.contexts+[{'role':'user','content':p.prompt}]),state)
        h.commit(p,'{}')
        p=h.prepare('a',json.dumps(state),1,now=3,retirement='stored summaries version 2')
        self.assertEqual(p.turns,1);self.assertFalse(any(m['role']=='assistant' for m in p.contexts))

    def test_thirty_rounds_exact_state_and_bounded_short_tails(self):
        h=AppendHistory();state=self.state();state['autonomousActivity']={'fatigue':2,'energy':98}
        for i in range(31):
            state['environment']['now']=100+i;state['goals'][0]['status']='completed' if i%3==0 else 'active'
            state['environment']['contacts'][0]['mayInitiate']=i%2==0
            state['environment']['sharedContext']['conversations'][0]['state']['emotion']=('happy','calm','angry')[i%3]
            if i%4==0:state['future']=[{'newEvidence':i,'unknownShape':[False,None,{'retain':'all'}]}]
            prefix=copy.deepcopy(h.history)
            p=h.prepare('authority',json.dumps(state),('tenant','bot'),now=i+1,retirement='same stored summaries')
            self.assertEqual(restore_append(p.contexts+[{'role':'user','content':p.prompt}]),state)
            if p.turns>1:self.assertEqual(p.contexts[:len(prefix)],prefix)
            self.assertLess(len(p.prompt),200);self.assertLessEqual(p.turns,MAX_TURNS)
            # A transport fixture, not a real model or fabricated receipt.
            h.commit(p,'{"fixtureDecision":"not an execution receipt"}')
        self.assertEqual(h.turns,1)


if __name__=='__main__':unittest.main()
