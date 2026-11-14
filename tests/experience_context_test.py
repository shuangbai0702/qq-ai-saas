"""Brief historical experience preserves source, receipts and full stored work."""
import copy,json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from context_transport import experience_view
from bot_pipeline import experience_context,ContextBuilder,conversation_context
from unittest.mock import patch


def fixture():
    return {'sampledAt':123456,'decisions':[{'cycleId':'cycle','at':123,'reason':'判断原因'*100,'action':{'goalId':'goal','kind':'research','objective':'目标'*200}}],
            'actions':[{'id':'action','kind':'research','sourceScope':'private:12345678','target':'private:12345678',
                'assignedAt':120,'finishedAt':124,'status':'partial','objective':'目标'*200,'summary':'执行过的观察'*100,
                'outerDecision':{'cycleId':'cycle','at':123,'reason':'判断原因'*100},
                'toolResults':[{'tool':'write','status':'unknown','detail':'回执未确认'*100}],
                'effects':[{'actionId':'stable','status':'unknown','target':'group:55555','receipt':{'status':'unknown','message_ids':[]}}],
                'evidence':[{'source':'bilibili','modality':'subtitles','limitations':['仅字幕，未观看画面']}],
                'unfinished':['未确认，不能重发：'+'解释'*150]}],
            'protocol':[{'actionId':'protocol','sourceScope':'private:12345678','receipt':{'status':'confirmed'}}]}


class BriefExperience(unittest.TestCase):
    def test_shared_journal_channel_switch_does_not_replay_execution_view(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE pipeline_events(event TEXT,at REAL,source TEXT,status TEXT,result TEXT)')
        raw=fixture();store=NS(db=db,shared_context=True,model_journals={},autonomous_experience=raw,reflection=NS(core_note=lambda *a,**k:{}))
        with patch('context_foundation.read',return_value={}),patch('semantic_runtime.filter_retired_context',side_effect=lambda s,c,v:v),patch('shared_context.delivery_context',return_value={}),patch('agreement_state.context_view',return_value=[]):
            private=conversation_context(store,{'memory':True,'ownerUin':'12345678'},'private:12345678','12345678','real input',200)
            group=conversation_context(store,{'memory':True,'ownerUin':'12345678'},'group:55555','55555555','different input',201)
            self.assertEqual(private['autonomous_experience'],group['autonomous_experience'])
            self.assertEqual(group['autonomous_experience']['actions'][0]['sourceScope'],'private:12345678')
            from decision_history import changes
            self.assertEqual(changes(private['autonomous_experience'],group['autonomous_experience']),[])
        self.assertEqual(store.autonomous_experience,raw)

    def test_terminal_unfinished_does_not_revive_history_but_unknown_receipt_survives(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE pipeline_events(event TEXT,at REAL,source TEXT,status TEXT,result TEXT)')
        raw=fixture();row=raw['actions'][0]
        row.update(status='blocked',effects=[])
        store=NS(db=db,autonomous_experience=raw)
        view=experience_context(store,'self',{'memory':True})
        self.assertEqual(view['actions'],[]);self.assertNotIn('sampledAt',view)
        row['effects']=[{'status':'unknown','receipt':{'status':'unknown'}}]
        view=experience_context(store,'self',{'memory':True})
        self.assertEqual([a['id'] for a in view['actions']],['action'])
        self.assertEqual(view['actions'][0]['effects'],row['effects'])
        self.assertEqual(raw['sampledAt'],123456)

    def test_prose_is_bounded_and_explicitly_marked_as_abridged(self):
        raw=fixture();raw_action=raw['actions'][0];view=experience_view(raw);action=view['actions'][0]
        self.assertEqual(view['view'],'brief_v1');self.assertIn('截断',view['meaning'])
        # D5: bounds stay but sit above the upstream authoring limits so
        # scope/time attribution qualifiers are never cut away; only truly
        # oversized prose is still visibly abridged.
        # D5: authoring-sized prose stays whole (attribution intact); only
        # genuinely oversized values are still visibly abridged.
        self.assertEqual(action['objective'],raw_action['objective']);self.assertNotIn('…',action['objective'])
        self.assertEqual(len(action['unfinished'][0]),201);self.assertEqual(len(action['toolResults'][0]['detail']),241)
        oversized=experience_view({'view':'raw','actions':[{'id':'big','objective':'超'*700}],'decisions':[]})['actions'][0]
        self.assertEqual(len(oversized['objective']),601);self.assertTrue(oversized['objective'].endswith('…'))
        self.assertNotIn('reason',action['outerDecision']);self.assertEqual(action['outerDecision']['cycleId'],'cycle')
        # D5: the brief never grows the ledger and stays explicitly marked.
        self.assertLessEqual(len(json.dumps(view)),len(json.dumps(raw)))

    def test_raw_records_immutable_and_projection_idempotent(self):
        raw=fixture();before=copy.deepcopy(raw);view=experience_view(raw)
        self.assertEqual(raw,before);self.assertEqual(experience_view(view),view)

    def test_identity_uncertain_effects_and_evidence_remain_exact(self):
        raw=fixture();view=experience_view(raw);a=view['actions'][0];old=raw['actions'][0]
        for key in ['id','kind','target','sourceScope','assignedAt','finishedAt','status','effects','evidence']:self.assertEqual(a[key],old[key])
        self.assertEqual(a['toolResults'][0]['status'],'unknown');self.assertEqual(view['protocol'],raw['protocol'])

    def test_historical_rationale_omitted_but_distinct_decision_identity_kept(self):
        for cycle,reason in [('other','原始不同判断'*50),('cycle','原始不同判断'*50)]:
            raw=fixture();raw['actions'][0]['outerDecision'].update(cycleId=cycle,reason=reason)
            decision=experience_view(raw)['actions'][0]['outerDecision'];self.assertNotIn('reason',decision);self.assertEqual(decision['cycleId'],cycle);self.assertEqual(decision['at'],123)

    def test_no_record_or_unfinished_entry_is_silently_discarded(self):
        raw=fixture();raw['actions']*=8;raw['actions'][0]['unfinished']=['阻碍'+str(i) for i in range(6)]
        view=experience_view(raw);self.assertEqual(len(view['actions']),8);self.assertEqual(len(view['actions'][0]['unfinished']),6)

    def test_disabled_memory_and_unknown_values_not_invented(self):
        self.assertIsNone(experience_view(None));view=experience_view({'actions':[{'id':'x','status':'failed','error':{'code':'no_access'}}]})
        self.assertEqual(view['actions'][0]['error'],{'code':'no_access'});self.assertNotIn('finishedAt',view['actions'][0])
        explicit=experience_view({'decisions':[{'at':None,'wait':{'kind':'time'}}],'actions':[{'id':'x','status':'blocked','finishedAt':None,'sourceScope':None}]})
        self.assertIsNone(explicit['decisions'][0]['at']);self.assertIsNone(explicit['actions'][0]['finishedAt']);self.assertIsNone(explicit['actions'][0]['sourceScope'])

    def test_shared_entry_scopes_still_filter_before_compaction(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE pipeline_events(event TEXT,at REAL,source TEXT,status TEXT,result TEXT)')
        raw=fixture();other={**raw['actions'][0],'id':'other','sourceScope':'group:55555','target':'group:55555'};raw['actions'].append(other)
        store=NS(db=db,autonomous_experience=raw);cfg={'memory':True,'ownerUin':'12345678'}
        private=experience_context(store,'private:12345678',cfg);outer=experience_context(store,'self',cfg)
        self.assertEqual(private['view'],'brief_v1');self.assertEqual([a['id'] for a in private['actions']],['action']);self.assertEqual(len(outer['actions']),2)
        self.assertEqual(store.autonomous_experience,raw)
        for origin in ['foreground','internal_task','autonomous_decision']:
            assembly=ContextBuilder({'scope':'private:12345678'},origin);assembly.contribute('memory',{'autonomous_experience':private});self.assertEqual(assembly.build()['autonomous_experience'],private)
        disabled=experience_context(store,'self',{'memory':False});self.assertEqual(disabled,{'memoryEnabled':False,'decisions':[],'actions':[]})


if __name__=='__main__':unittest.main()
