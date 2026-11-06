"""Outer 35-effective-cycle transport: persistence, permits, fail-closed resets."""
import copy,hashlib,json,sqlite3,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from outer_journal import PersistentOuterHistory,OuterJournalDenied,OuterCapacityBlocked,OuterRecoveryUnavailable,checkpoint_digest,valid_checkpoint
from decision_context import decision_view

def state(turn=1):
    return {'environment':{'now':100+turn,'self':{'interests':[{'topic':'fish','heat':.42}]},'sharedContext':{'memoryEnabled':True,'conversations':[{'identity':{'scope':'group:12345678'},'state':{'facts':[],'conversation_summaries':[{'id':1,'summary':'background'*20}]},'recent_messages':[{'source_scope':'group:12345678','source_event':str(turn),'sender_id':'12345678','at':98+turn,'user':'real message %d'%turn}]}]}},'goals':[{'id':'g','status':'active','successCondition':'receipt'}],'future':{'r':'keep'}}

def checkpoint(scope_key,seq=35,checkpoint_id='cp-1'):
    value={'checkpointId':checkpoint_id,'throughSeq':seq,'cycleIds':['cycle-%d'%i for i in range(35)]}
    value['digest']=checkpoint_digest(scope_key,value)
    return value

class Outer(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.key=json.dumps(('tenant','bot',1))
    def journal(self,phase='orientation'):return PersistentOuterHistory(self.db,self.key,phase)

    def test_first_baseline_then_deltas_then_restart_restores_prefix(self):
        h=self.journal()
        p=h.prepare('authority',json.dumps(decision_view(state(1))),('tenant','bot',1),now=1,retirement='epoch:1')
        self.assertEqual(p.reset_reason,'');self.assertTrue(h.commit(p,'{"ok":1}'))
        prefix=[copy.deepcopy(m) for m in h.history]
        second=state(2)
        p2=h.prepare('authority',json.dumps(decision_view(second)),('tenant','bot',1),now=2,retirement='epoch:1')
        self.assertEqual(p2.reset_reason,'');self.assertGreater(p2.turns,1)
        self.assertTrue(h.commit(p2,'{"ok":2}'))
        restored=self.journal()
        self.assertEqual(restored.history,h.history);self.assertEqual(restored.turns,h.turns)
        self.assertEqual(restored.records,h.records);self.assertFalse(restored.recovery_error)

    def test_idle_time_and_request_counts_never_reset(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        p=h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=1+8*24*3600,retirement='epoch:1')
        self.assertEqual(p.reset_reason,'')
        for i in range(60):
            p=h.prepare('a',json.dumps(decision_view(state(i+3))),('tenant','bot',1),now=i+2,retirement='epoch:1')
            self.assertEqual(p.reset_reason,'');h.commit(p,'{}')
        self.assertGreater(h.turns,35)

    def test_summary_write_rides_as_delta_not_reset(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        changed=state(2);changed['environment']['sharedContext']['conversations'][0]['state']['conversation_summaries'].append({'id':2,'summary':'new rolling summary'})
        p=h.prepare('a',json.dumps(decision_view(changed)),('tenant','bot',1),now=2,retirement='epoch:1')
        self.assertEqual(p.reset_reason,'');self.assertTrue(h.commit(p,'{}'))
        restored=json.loads(p.contexts[-1]['content']) if p.contexts[-1]['content'].startswith('{') else json.loads(p.prompt)

    def test_forgetting_epoch_requires_checkpoint_and_consumes_once(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        with self.assertRaises(OuterJournalDenied):
            h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:2')
        permit=checkpoint(self.key)
        p=h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:2',permit=permit)
        self.assertEqual(p.reset_reason,'memory_epoch_changed');self.assertTrue(h.commit(p,'{}'))
        # The same checkpoint id cannot serve the next rebuild of this phase.
        with self.assertRaises(OuterJournalDenied):
            h.prepare('a',json.dumps(decision_view(state(3))),('tenant','bot',1),now=3,retirement='epoch:3',permit=checkpoint(self.key))
        p2=h.prepare('a',json.dumps(decision_view(state(3))),('tenant','bot',1),now=3,retirement='epoch:3',permit=checkpoint(self.key,checkpoint_id='cp-2'))
        self.assertEqual(p2.reset_reason,'memory_epoch_changed');self.assertTrue(h.commit(p2,'{}'))

    def test_tampered_or_reused_checkpoint_rejected(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        bad=checkpoint(self.key);bad['throughSeq']=34
        with self.assertRaises(OuterJournalDenied):
            h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:2',permit=bad)
        wrong_scope=checkpoint(json.dumps(('tenant','other',1)))
        with self.assertRaises(OuterJournalDenied):
            h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:2',permit=wrong_scope)
        self.assertFalse(valid_checkpoint(self.key,{'checkpointId':'x','throughSeq':70,'cycleIds':['c']*35,'digest':'no'}))

    def test_aborted_candidate_does_not_consume_checkpoint(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        permit=checkpoint(self.key)
        p=h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:2',permit=permit)
        self.assertTrue(h.abort_candidate(p))
        p2=h.prepare('a',json.dumps(decision_view(state(3))),('tenant','bot',1),now=3,retirement='epoch:2',permit=checkpoint(self.key,checkpoint_id='cp-1'))
        self.assertTrue(h.commit(p2,'{}'))

    def test_capacity_growth_blocks_instead_of_silent_reset_until_checkpoint(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        growing=state(2);growing['goals'][0]['progress']='y'*300000
        with self.assertRaises(OuterCapacityBlocked):
            h.prepare('a',json.dumps(decision_view(growing)),('tenant','bot',1),now=2,retirement='epoch:1')
        permit=checkpoint(self.key)
        p=h.prepare('a',json.dumps(decision_view(growing)),('tenant','bot',1),now=2,retirement='epoch:1',permit=permit)
        self.assertTrue(h.commit(p,'{}'))
        restored=self.journal();self.assertEqual(restored.records,h.records)

    def test_corrupt_snapshot_fails_closed(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        self.db.execute("UPDATE fishbot_outer_journals SET payload='{broken' WHERE scope=? AND phase='orientation'",(self.key,));self.db.commit()
        broken=self.journal()
        with self.assertRaises(OuterRecoveryUnavailable):
            broken.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:1')
        with self.assertRaises(OuterRecoveryUnavailable):
            broken.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:1',permit=checkpoint(self.key))

    def test_seed_from_peer_shares_committed_prefix_only(self):
        orientation=self.journal('orientation')
        orientation.commit(orientation.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        planning=self.journal('planning')
        self.assertTrue(planning.seed_from(orientation))
        p=planning.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:1')
        self.assertEqual(p.reset_reason,'')
        # Each phase consumes its own checkpoint copy.
        self.assertFalse(planning._permit_consumed('cp-1'))

    def test_authenticated_web_rebaseline_is_one_time_per_phase(self):
        h=self.journal()
        h.commit(h.prepare('a',json.dumps(decision_view(state())),('tenant','bot',1),now=1,retirement='epoch:1'),'{}')
        # No license: still denied after forgetting.
        with self.assertRaises(OuterJournalDenied):
            h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:2')
        p=h.prepare('a',json.dumps(decision_view(state(2))),('tenant','bot',1),now=2,retirement='epoch:2',web_permit='audit-9')
        self.assertEqual(p.reset_reason,'memory_epoch_changed');self.assertTrue(h.commit(p,'{}'))
        # The same web proof cannot rebaseline the phase twice.
        h2=self.journal()
        h2.retire('operator_retire')
        with self.assertRaises(OuterJournalDenied):
            h2.prepare('a',json.dumps(decision_view(state(3))),('tenant','bot',1),now=3,retirement='epoch:2',web_permit='audit-9')
        h3=self.journal()
        h3.retire('operator_retire')
        p3=h3.prepare('a',json.dumps(decision_view(state(3))),('tenant','bot',1),now=3,retirement='epoch:2',web_permit='audit-10')
        self.assertTrue(h3.commit(p3,'{}'))
        # Web proofs consume once per phase (mirrors checkpointId+phase):
        # planning may use the same save for its own single rebuild.
        planning=self.journal('planning');planning.retire('operator_retire')
        pp=planning.prepare('a',json.dumps(decision_view(state(3))),('tenant','bot',1),now=3,retirement='epoch:2',web_permit='audit-10')
        self.assertTrue(planning.commit(pp,'{}'))
        planning2=self.journal('planning');planning2.retire('operator_retire')
        with self.assertRaises(OuterJournalDenied):
            planning2.prepare('a',json.dumps(decision_view(state(4))),('tenant','bot',1),now=4,retirement='epoch:2',web_permit='audit-10')

if __name__=='__main__':unittest.main()
