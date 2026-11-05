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

if __name__=='__main__':unittest.main()
