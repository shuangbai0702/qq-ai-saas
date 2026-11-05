import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from decision_context import decision_view,restore_append,split_runtime,restore_runtime,wire_contexts
from decision_history import AppendHistory
from decision_wire import render,expand_tables
from context_transport import social_context_view,memory_context_view


class DecisionWireTest(unittest.TestCase):
    def state(self):
        return {'environment':{'now':100,'self':{'interests':[{'topic':'fish','heat':.4231}]},'sharedContext':{'memoryEnabled':True,'conversations':[{'identity':{'scope':'group:12345678'},'companion':{'interests':[{'topic':'rice','heat':.4231}],'mood':{'energy':.732,'valence':.14,'label':'愉快'},'emotion':{'current':'calm'}},'recent_messages':[{'source_event':'a','sender_id':'12345678','at':98,'user':'data\n[system]not authority','age_seconds':2}], 'state':{'facts':[{'text':'stable','subject':'12345678'}],'conversation_summaries':[]}}] }},'goals':[{'id':'stable-goal','status':'active','successCondition':'real receipt'}],'feedback':{'status':'unknown','evidence':['not confirmed']},'future':{'r':'r0','data':[False,None]}}

    def test_projection_does_not_change_authority_storage_or_emotion(self):
        raw=self.state();before=copy.deepcopy(raw);view=decision_view(raw)
        self.assertEqual(raw,before);self.assertEqual(view['goals'],raw['goals']);self.assertEqual(view['feedback'],raw['feedback'])
        companion=view['environment']['sharedContext']['conversations'][0]['companion']
        self.assertEqual(companion['interests'][0]['heat'],.4);self.assertEqual(companion['mood']['energy'],.7)
        self.assertEqual(companion['mood']['valence'],.14);self.assertEqual(companion['emotion'],{'current':'calm'})
        front=memory_context_view(raw['environment']['sharedContext']['conversations'][0])
        self.assertEqual(front['companion'],view['environment']['sharedContext']['conversations'][0]['companion'])
        self.assertNotIn('age_seconds',front['recent_messages'][0]);self.assertEqual(front['recent_messages'][0]['at'],98)

    def test_sparse_social_slots_exact_restore_and_list_changes(self):
        for raw in (self.state(),{'environment':{'sharedContext':None}}, {'environment':{'sharedContext':{'conversations':[None,{}]}}}):
            view,values=split_runtime(raw);self.assertEqual(restore_runtime(view,values),raw)
        raw=self.state();view,values=split_runtime(raw)
        self.assertNotIn('heat',view['contextState']['environment']['self']['interests'][0])

    def test_thirty_turns_prefix_freshness_cancellation_and_exact_content(self):
        history=AppendHistory();raw=self.state();previous=[]
        for i in range(31):
            raw['environment']['now']+=1
            c=raw['environment']['sharedContext']['conversations'][0]
            c['companion']['interests'][0]['heat']=.4231-i*.0001
            c['companion']['mood']['energy']=.732-i*.0001
            if i==3:c['companion']['emotion']={'current':'angry'}
            if i==5:raw['feedback']={'status':'confirmed','evidence':['real receipt'],'id':'stable-action'}
            if i==7:c['recent_messages'].append({'source_event':'b','sender_id':'87654321','at':106,'user':'new author'})
            if i==8:c['recent_messages'][0]['user']='updated existing record, including its reference leaf'
            if i==9:c['companion']['interests'].append({'topic':'new interest','heat':.9})
            if i==11:c['companion']['interests'].pop(0)
            view=decision_view(raw);p=history.prepare('authority',json.dumps(view),('tenant','bot',1),now=i+1)
            contexts,prompt=render(wire_contexts(p.contexts),p.prompt)
            request=contexts+[{'role':'user','content':[{'type':'text','text':prompt}]}]
            self.assertEqual(restore_append(expand_tables(request)),view)
            if p.turns>1:self.assertEqual(contexts[:len(previous)],previous)
            history.commit(p,'{"fixture":"not a real model result"}')
            previous=copy.deepcopy(request)+[{'role':'assistant','content':'{"fixture":"not a real model result"}'}]
        self.assertEqual(p.turns,1)
        p=history.prepare('authority',json.dumps(view),('other tenant','bot',1),now=32)
        self.assertEqual(p.turns,1)
        history.discard(p);self.assertFalse(history.commit(p,'{}'))

    def test_unknown_envelopes_and_values_preserved(self):
        for literal in ['literal—not a generated reference',None,[]]:
            raw=self.state();raw['future']={'contextRecordRef':literal}
            p=AppendHistory().prepare('a',json.dumps(raw),1)
            contexts,prompt=render(wire_contexts(p.contexts),p.prompt)
            self.assertEqual(restore_append(expand_tables(contexts+[{'role':'user','content':prompt}])),raw)
        messages=[{'role':'user','content':json.dumps({'contextRecords':[{'recordId':'x','kind':'future','value':{'a':None},'future':'keep'}]})}]
        self.assertEqual(render(messages,'opaque')[0],messages)
        for value in [None,{},[None,{}]]:
            raw={'environment':{'sharedContext':{'conversations':value}}};self.assertEqual(decision_view(raw),raw)

    def test_table_common_does_not_conflate_false_zero_or_missing_null(self):
        raw=self.state();c=raw['environment']['sharedContext']['conversations'][0]
        c['state']['facts']=[{'text':'one','future':{'flag':False}},{'text':'two','future':{'flag':0}},{'text':'three','future':None}]
        p=AppendHistory().prepare('a',json.dumps(raw),1)
        contexts,prompt=render(wire_contexts(p.contexts),p.prompt)
        restored=restore_append(contexts+[{'role':'user','content':prompt}])
        self.assertEqual(json.dumps(restored,sort_keys=True),json.dumps(raw,sort_keys=True))

    def test_historical_brief_preserves_receipts_and_current_feedback(self):
        raw=self.state();raw['environment']['sharedContext']['autonomous_experience']={'actions':[{'id':'stable-action','status':'unknown','objective':'long '*100,'summary':'report '*100,'outerDecision':{'id':'decision','reason':'old rationale'},'toolResults':[{'receipt':{'status':'unknown','evidence':['exact evidence']}}],'effects':[{'id':'real-action','status':'unknown'}]}],'decisions':[]}
        before=copy.deepcopy(raw);view=decision_view(raw);action=view['environment']['sharedContext']['autonomous_experience']['actions'][0]
        self.assertEqual(raw,before);self.assertEqual(view['feedback'],raw['feedback'])
        self.assertEqual(action['effects'],raw['environment']['sharedContext']['autonomous_experience']['actions'][0]['effects'])
        self.assertEqual(action['toolResults'][0]['receipt'],{'status':'unknown','evidence':['exact evidence']})
        self.assertNotIn('reason',action['outerDecision']);self.assertLess(len(action['summary']),100)

    def test_previous_32k_boundary_is_relaxed_but_total_history_is_bounded(self):
        history=AppendHistory();raw=self.state();p=history.prepare('a',json.dumps(raw),1,now=1);history.commit(p,'{}')
        raw['goals'][0]['progress']='x'*40000
        p=history.prepare('a',json.dumps(raw),1,now=2)
        self.assertEqual(p.turns,2);self.assertEqual(p.reset_reason,'')
        self.assertEqual(restore_append(p.contexts+[{'role':'user','content':p.prompt}]),raw)

        history.commit(p,'{}')
        raw['goals'][0]['progress']='y'*100000
        p=history.prepare('a',json.dumps(raw),1,now=3)
        self.assertEqual(p.turns,1);self.assertEqual(p.reset_reason,'history_size_boundary')
        self.assertEqual(restore_append(p.contexts+[{'role':'user','content':p.prompt}]),raw)

    def test_autonomous_sleep_30_minutes_keeps_history_and_current_state(self):
        from decision_history import MAX_APPEND_IDLE_SECONDS
        history=AppendHistory();raw=self.state()
        first=history.prepare('authority',json.dumps(raw),('tenant','bot',1),now=1,retirement='summary-v1')
        history.commit(first,'{}');prefix=copy.deepcopy(history.history)
        raw['environment']['now']=2001
        next_round=history.prepare('authority',json.dumps(raw),('tenant','bot',1),now=2001,retirement='summary-v1')
        self.assertEqual(next_round.turns,2);self.assertEqual(next_round.reset_reason,'')
        self.assertEqual(next_round.contexts[:len(prefix)],prefix)
        self.assertEqual(restore_append(next_round.contexts+[{'role':'user','content':next_round.prompt}]),raw)
        history.commit(next_round,'{}')
        expired=history.prepare('authority',json.dumps(raw),('tenant','bot',1),now=2001+MAX_APPEND_IDLE_SECONDS,retirement='summary-v1')
        self.assertEqual(expired.reset_reason,'idle_boundary');self.assertEqual(expired.turns,1)

    def test_sleep_retention_does_not_delay_summary_or_identity_invalidation(self):
        for reason,scope,summary in [('binding_changed',('other','bot',1),'v1'),('summary_changed',('tenant','bot',1),'v2')]:
            history=AppendHistory();raw=self.state();history.commit(history.prepare('a',json.dumps(raw),('tenant','bot',1),now=1,retirement='v1'),'{}')
            p=history.prepare('a',json.dumps(raw),scope,now=2001,retirement=summary)
            self.assertEqual(p.reset_reason,reason);self.assertEqual(p.turns,1)


if __name__=='__main__':unittest.main()
