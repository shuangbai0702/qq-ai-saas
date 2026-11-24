"""Transport continuity must preserve current state, ownership and retirement."""
import copy,json,random,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from decision_history import changes,apply_changes,encode

class History(unittest.TestCase):
    def test_patch_roundtrip_types_deletions_lists_and_unknowns(self):
        pairs=[({'a':True},{'a':1}),({'a':1},{'a':1.0}),({'x':[1,2,3]},{'x':[None]}),({'a':None},{'b':False}),({'x':{}},{'x':[{'next':True}]})]
        for old,new in pairs:
            before=copy.deepcopy(old);ops=changes(old,new)
            self.assertEqual(encode(apply_changes(old,ops)),encode(new));self.assertEqual(old,before)

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
