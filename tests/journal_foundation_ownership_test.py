"""A damaged legacy wire recovers its checkpoint-owned basis by append only."""
import copy,hashlib,sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import journal_storage as storage
from context_foundation import FOUNDATION_CURRENT
from context_protocol import READONLY_CURRENT,model_json
from readonly_journal import ReadonlyJournal


class JournalFoundationOwnership(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.scope='bot_shared';self.j=ReadonlyJournal(self.db,self.scope)
        self.base=[{'role':'system','content':'exact committed System'},
                   {'role':'user','content':'actual current question'}]
        self.state={'memory_foundation':{
            'conversation_summaries':[{'id':1,'level':'big','summary':'checkpoint big summary'},
                                      {'id':2,'level':'small','summary':'checkpoint small summary'}],
            'facts':[{'value':'checkpoint fact'}]},'active_agreements':[]}
        initial=self.j.prepare(self.incoming(self.state),'binding',now=1)
        self.assertTrue(self.j.commit(initial))
        self.packets=self.regions(initial.wire)
        self.assertEqual(len(self.packets),3)

    def tearDown(self):self.db.close()
    def incoming(self,state):
        return copy.deepcopy(self.base)+[{'role':'user','content':READONLY_CURRENT+'\n'+model_json(state)}]
    def regions(self,wire):
        return [m for m in wire if m.get('role')=='user' and
                isinstance(m.get('content'),str) and m['content'].startswith(FOUNDATION_CURRENT+'\n')]
    def damage_legacy_wire(self,keep=()):
        # Reproduce the actual persisted inconsistency: metadata survived the
        # old background purge while some/all submitted fixed packets vanished.
        value=storage.read_snapshot(self.db,self.j.scope_key)
        value['wire']=[m for m in value['wire'] if m not in self.packets or m in keep]
        payload,blobs=storage.encode(value);self.assertEqual(blobs,{})
        self.db.execute('UPDATE fishbot_readonly_journals SET payload=?,sha=? WHERE scope=?',
                        (payload,hashlib.sha256(payload.encode()).hexdigest(),self.j.scope_key))
        self.db.commit();self.j=ReadonlyJournal(self.db,self.scope)
        self.assertIsNone(self.j.recovery_error)
        return copy.deepcopy(self.j.last.wire)

    def assert_prefix_and_basis(self,candidate,prefix):
        self.assertEqual(model_json(candidate.wire[:len(prefix)]),model_json(prefix))
        self.assertEqual(self.regions(candidate.wire),self.packets)
        self.assertEqual(candidate.state['memory_foundation'],self.state['memory_foundation'])
        self.assertIsNone(candidate.reconstruction_permit)
        self.assertEqual(candidate.reason,'')

    def test_missing_regions_append_once_and_survive_restart(self):
        prefix=self.damage_legacy_wire()
        candidate=self.j.prepare(self.incoming(self.state),'binding',now=2)
        self.assert_prefix_and_basis(candidate,prefix)
        self.assertTrue(self.j.commit(candidate));wire=copy.deepcopy(candidate.wire)
        for restart in (False,True):
            if restart:self.j=ReadonlyJournal(self.db,self.scope)
            with self.subTest(restart=restart):
                repeated=self.j.prepare(self.incoming(self.state),'binding',now=3)
                self.assertEqual(repeated.wire,wire)
                self.assertTrue(self.j.commit(repeated))

    def test_partial_missing_only_appends_absent_regions(self):
        prefix=self.damage_legacy_wire(keep=[self.packets[0]])
        candidate=self.j.prepare(self.incoming(self.state),'binding',now=2)
        self.assert_prefix_and_basis(candidate,prefix)
        self.assertEqual(self.regions(candidate.wire[len(prefix):]),self.packets[1:])

    def test_repair_uses_frozen_checkpoint_metadata_not_changed_database_view(self):
        prefix=self.damage_legacy_wire()
        newer=copy.deepcopy(self.state)
        newer['memory_foundation']['facts']=[{'value':'new database fact awaiting checkpoint'}]
        candidate=self.j.prepare(self.incoming(newer),'binding',now=2)
        self.assert_prefix_and_basis(candidate,prefix)
        self.assertNotIn('new database fact awaiting checkpoint',model_json(candidate.wire))

    def test_failed_persistence_and_restart_retry_do_not_lose_repair(self):
        prefix=self.damage_legacy_wire()
        persisted=self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchone()
        candidate=self.j.prepare(self.incoming(self.state),'binding',now=2)
        self.assert_prefix_and_basis(candidate,prefix)
        with patch.object(self.j,'_persist',return_value=False):
            self.assertFalse(self.j.commit(candidate))
        self.assertEqual(self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchone(),persisted)
        self.j=ReadonlyJournal(self.db,self.scope)
        retry=self.j.prepare(self.incoming(self.state),'binding',now=3)
        self.assert_prefix_and_basis(retry,prefix)
        self.assertTrue(self.j.commit(retry))
        restored=ReadonlyJournal(self.db,self.scope)
        self.assertEqual(restored.last.wire,retry.wire)


if __name__=='__main__':unittest.main()
