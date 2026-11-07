"""Transport continuity must preserve current state, ownership and retirement."""
import copy,json,random,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from decision_history import DecisionHistory,changes,apply_changes,encode,MAX_TURNS,MAX_ADDED_CHARS

class History(unittest.TestCase):
    def snapshot(self):
        return {'environment':{'identity':{'owner':'123'},'now':1,'sharedContext':{'conversations':[{'state':{'conversation_summaries':[{'text':'long background'}]},'recent_messages':[{'id':1,'text':'a'}]}]}},'facts':['fact '*500], 'unknown':{'keep':None}}

    def test_patch_roundtrip_types_deletions_lists_and_unknowns(self):
        pairs=[({'a':True},{'a':1}),({'a':1},{'a':1.0}),({'x':[1,2,3]},{'x':[None]}),({'a':None},{'b':False}),({'x':{}},{'x':[{'next':True}]})]
        for old,new in pairs:
            before=copy.deepcopy(old);ops=changes(old,new)
            self.assertEqual(encode(apply_changes(old,ops)),encode(new));self.assertEqual(old,before)

    def test_continuation_keeps_complete_prior_messages_and_latest_state(self):
        h=DecisionHistory();old=self.snapshot();first=h.prepare('authority',encode(old),('tenant','bot',1),now=1)
        self.assertTrue(h.commit(first,'{"action":null}'));prefix=copy.deepcopy(h.history)
        new=copy.deepcopy(old);new['environment']['now']=2;new['environment']['sharedContext']['conversations'][0]['recent_messages'].append({'id':2,'text':'new'})
        second=h.prepare('authority',encode(new),('tenant','bot',1),now=2)
        self.assertEqual(second.contexts,prefix);self.assertEqual(apply_changes(old,json.loads(second.prompt)['stateChanges']),new)
        second.contexts[0]['content']='provider mutates request copy'
        self.assertEqual(h.history,prefix)

    def test_summary_retirement_forgetting_and_authority_reset(self):
        for kind in ('summary','epoch','tenant','system','memory_disabled'):
            h=DecisionHistory();old=self.snapshot();h.commit(h.prepare('authority',encode(old),('tenant','bot',1),now=1),'{}')
            new=copy.deepcopy(old);scope=('tenant','bot',1);system='authority'
            if kind=='summary':new['environment']['sharedContext']['conversations'][0]['state']['conversation_summaries']=[{'text':'new compressed history'}]
            if kind=='epoch':scope=('tenant','bot',2)
            if kind=='tenant':scope=('other','bot',1)
            if kind=='system':system='changed permissions'
            if kind=='memory_disabled':new['environment']['sharedContext']['memoryEnabled']=False
            prepared=h.prepare(system,encode(new),scope,now=2)
            self.assertEqual(prepared.contexts,[]);self.assertEqual(json.loads(prepared.prompt),new)

    def test_stale_and_cancelled_owner_cannot_write_back(self):
        h=DecisionHistory();a=h.prepare('a',encode(self.snapshot()),1,now=1);b=h.prepare('a',encode(self.snapshot()),2,now=2)
        self.assertFalse(h.commit(a,'{}'));self.assertTrue(h.commit(b,'{}'));h.discard(a);self.assertIsNotNone(h.snapshot)
        c=h.prepare('a',encode(self.snapshot()),2,now=3);h.discard(c);self.assertFalse(h.commit(c,'{}'));self.assertEqual(h.history,[])

    def test_bounds_idle_and_restart_rebuild_from_authority(self):
        h=DecisionHistory();state=self.snapshot()
        for i in range(MAX_TURNS):
            state['environment']['now']=i;p=h.prepare('a',encode(state),1,now=i+1);h.commit(p,'{}')
        self.assertEqual(h.prepare('a',encode(state),1,now=MAX_TURNS+1).contexts,[])
        self.assertEqual(h.prepare('a',encode(state),1,now=4000).contexts,[])
        self.assertEqual(DecisionHistory().prepare('a',encode(state),1).contexts,[])
        h=DecisionHistory();h.commit(h.prepare('a',encode(state),1,now=1),'{}');state['largeNewField']='z'*(MAX_ADDED_CHARS+1)
        self.assertEqual(h.prepare('a',encode(state),1,now=2).contexts,[])

    def test_invalid_decision_is_not_an_assistant_record(self):
        h=DecisionHistory();p=h.prepare('a',encode(self.snapshot()),1)
        with self.assertRaises(ValueError):h.commit(p,'not JSON')
        self.assertEqual(h.history,[])

    def test_randomized_projection_exactly_matches_current_state(self):
        rng=random.Random(17)
        def value(depth):
            if depth==0:return rng.choice([None,False,True,0,1,'x','中文'])
            return rng.choice([lambda:[value(depth-1) for _ in range(rng.randrange(4))],lambda:{str(i):value(depth-1) for i in range(rng.randrange(4))},lambda:value(0)])()
        for _ in range(200):
            a,b=value(3),value(3)
            self.assertEqual(encode(apply_changes(a,changes(a,b))),encode(b))

    def events(self,count,start=0,scope='group:1'):
        return [{'role':'member','source_scope':scope,'source_event':start+i,'sender_id':100+i,'text':'message %d text'%(start+i)} for i in range(count)]

    def test_sliding_window_shift_appends_tail_without_resending_history(self):
        state={'recent_messages':self.events(30),'conversation_timeline':self.events(40,100)}
        before=copy.deepcopy(state)
        after=copy.deepcopy(state);after['recent_messages']=after['recent_messages'][1:]+[{'role':'member','source_scope':'group:1','source_event':30,'sender_id':130,'text':'message 30 text'}]
        ops=changes(before,after,())
        self.assertTrue(all(op[1]!=['recent_messages'] for op in ops))
        self.assertEqual(len(ops),2)
        self.assertEqual(ops[0],['remove',['recent_messages',0]])
        self.assertEqual(encode(apply_changes(before,ops)),encode(after));self.assertEqual(before,state)
        for shift in range(2,6):
            after={'recent_messages':self.events(30+shift,shift)}
            ops=changes(state,after,())
            self.assertLessEqual(len(ops),1+shift)
            self.assertEqual(encode(apply_changes(state,ops)),encode(after))

    def test_same_event_update_is_one_in_place_set(self):
        state={'recent_messages':self.events(10)}
        after=copy.deepcopy(state);after['recent_messages'][4]['text']='revised body for event 4'
        ops=changes(state,after,())
        self.assertEqual(ops,[['set',['recent_messages',4,'text'],'revised body for event 4']])
        self.assertEqual(encode(apply_changes(state,ops)),encode(after))

    def test_growing_timeline_appends_only_new_events(self):
        state={'conversation_timeline':self.events(20,100)}
        after={'conversation_timeline':self.events(26,100)}
        ops=changes(state,after,())
        self.assertEqual(len(ops),6)
        self.assertTrue(all(op[0]=='set' and op[1][:-1]==['conversation_timeline'] for op in ops))
        self.assertEqual(encode(apply_changes(state,ops)),encode(after))

    def test_head_insertion_keeps_exact_projection(self):
        state={'recent_messages':self.events(5)}
        after={'recent_messages':[{'role':'member','source_scope':'group:1','source_event':99,'sender_id':199,'text':'urgent'}]+self.events(5)}
        ops=changes(state,after,())
        self.assertEqual(encode(apply_changes(state,ops)),encode(after))

    def test_scope_disambiguates_equal_event_ids(self):
        state={'recent_cross_session_messages':self.events(3,0,scope='group:1')+self.events(3,0,scope='private:2')}
        after=copy.deepcopy(state);after['recent_cross_session_messages'][0]['text']='group event 0 revised'
        ops=changes(state,after,())
        self.assertEqual(ops,[['set',['recent_cross_session_messages',0,'text'],'group event 0 revised']])
        self.assertEqual(encode(apply_changes(state,ops)),encode(after))

    def test_randomized_event_records_roundtrip(self):
        rng=random.Random(41)
        for _ in range(300):
            count=rng.randrange(0,25)
            rows=[{'source_scope':'group:%d'%rng.randrange(3),'source_event':rng.randrange(30),'sender_id':rng.randrange(8),'text':'v%d'%rng.randrange(6)} for _ in range(count)]
            operation=rng.randrange(4)
            after=copy.deepcopy(rows)
            if operation==0 and after:after.pop(0)
            elif operation==1:after.append({'source_scope':'group:9','source_event':rng.randrange(30),'sender_id':9,'text':'tail'})
            elif operation==2 and after:after[rng.randrange(len(after))]['text']='updated'
            else:after=rows[:rng.randrange(len(rows)+1)]
            self.assertEqual(encode(apply_changes(rows,changes(rows,after,()))),encode(after))

if __name__=='__main__':unittest.main()
