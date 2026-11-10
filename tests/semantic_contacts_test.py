"""Contacts retain scheduling clocks while exact retired raw copies leave."""
import sqlite3,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot')]
from behavior import BehaviorStore
import initiative,semantic_batch
from context_protocol import model_json

SCOPE='private:12345678'
SUBJECT='12345678'


class ContactsRetirement(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite');self.db=self.store.db
        semantic_batch.initialize(self.db)

    def tearDown(self):self.store.close();self.temp.cleanup()

    def retire(self,event='old',scope=SCOPE,shared=False):
        batch_scope='bot_shared' if shared else scope
        identifier=model_json([scope,event]) if shared else event
        with self.db:
            self.db.execute("INSERT INTO memory_semantic_batches VALUES(1,?,'digest',?,0,'retired',0,0)",(batch_scope,model_json([semantic_batch.event_key(batch_scope,identifier)])))
            self.assertTrue(semantic_batch.purge(self.db,1))

    def contact(self,scope=SCOPE,event='old',at=100):
        initiative.incoming(self.db,scope,SUBJECT,'99999999','raw incoming',at,event=event)
        initiative.outgoing(self.db,scope,'raw outgoing',at+1,event=event)

    def test_exact_shared_batch_erases_only_its_contact_copies_and_keeps_clocks(self):
        self.contact();self.contact('private:87654321')
        self.db.execute('UPDATE contacts SET last_attempt=88,unanswered=2,opted_out=1 WHERE scope=?',(SCOPE,));self.db.commit()
        before=self.db.execute('SELECT last_user,last_bot,last_attempt,unanswered,opted_out FROM contacts WHERE scope=?',(SCOPE,)).fetchone()
        self.retire(shared=True)
        self.assertEqual(self.db.execute('SELECT last_text,last_output,last_user_event,last_bot_event FROM contacts WHERE scope=?',(SCOPE,)).fetchone(),('','',None,None))
        self.assertEqual(self.db.execute('SELECT last_user,last_bot,last_attempt,unanswered,opted_out FROM contacts WHERE scope=?',(SCOPE,)).fetchone(),before)
        self.assertEqual(self.db.execute("SELECT last_text,last_output FROM contacts WHERE scope='private:87654321'").fetchone(),('raw incoming','raw outgoing'))

    def test_newer_event_replaces_attribution_and_is_not_retired(self):
        self.contact();self.contact(event='new',at=200);self.retire()
        self.assertEqual(self.db.execute('SELECT last_text,last_output,last_user_event,last_bot_event FROM contacts WHERE scope=?',(SCOPE,)).fetchone(),('raw incoming','raw outgoing','new','new'))

    def test_missing_event_clears_prior_attribution_in_both_directions(self):
        self.contact()
        initiative.incoming(self.db,SCOPE,SUBJECT,'99999999','unbound incoming',200)
        initiative.outgoing(self.db,SCOPE,'unbound outgoing',201)
        self.retire()
        self.assertEqual(self.db.execute('SELECT last_text,last_output,last_user_event,last_bot_event FROM contacts WHERE scope=?',(SCOPE,)).fetchone(),('unbound incoming','unbound outgoing',None,None))

    def test_legacy_incoming_uses_exact_timestamp_author_and_retains_unproved_output(self):
        initiative.incoming(self.db,SCOPE,SUBJECT,'99999999','legacy input',100)
        initiative.outgoing(self.db,SCOPE,'legacy output',101)
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,SUBJECT,'old','a separately recorded input','answer',100));self.db.commit()
        self.retire()
        self.assertEqual(self.db.execute('SELECT last_text,last_output,last_user,last_bot FROM contacts WHERE scope=?',(SCOPE,)).fetchone(),('','legacy output',100,101))

    def test_legacy_different_author_or_time_cannot_grant_retirement(self):
        for subject,at in ((SUBJECT,99),('87654321',100)):
            with self.subTest(subject=subject,at=at):
                self.db.execute('DELETE FROM memory_semantic_batches');self.db.execute('DELETE FROM memory_pending');self.db.commit()
                initiative.incoming(self.db,SCOPE,SUBJECT,'99999999','legacy retained',100)
                self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',(SCOPE,subject,'old','same words irrelevant','answer',at));self.db.commit()
                self.retire()
                self.assertEqual(self.db.execute('SELECT last_text FROM contacts WHERE scope=?',(SCOPE,)).fetchone()[0],'legacy retained')

    def test_schema_migrates_legacy_contacts_with_nullable_attribution(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE contacts(scope TEXT PRIMARY KEY,subject TEXT,self_id TEXT,last_user REAL,last_bot REAL DEFAULT 0,last_attempt REAL DEFAULT 0,unanswered INTEGER DEFAULT 0,opted_out INTEGER DEFAULT 0,last_text TEXT,last_output TEXT)')
            db.execute("INSERT INTO contacts(scope,last_text,last_output) VALUES('private:12345678','legacy input','legacy output')")
            initiative.initialize(db);initiative.initialize(db)
            self.assertEqual(db.execute('SELECT last_text,last_output,last_user_event,last_bot_event FROM contacts').fetchone(),('legacy input','legacy output',None,None))

    def test_ambiguous_legacy_timestamp_does_not_become_proof_as_rows_leave(self):
        initiative.incoming(self.db,SCOPE,SUBJECT,'99999999','unattributed',100)
        for event in ('old','also-old'):
            self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,100)',(SCOPE,SUBJECT,event,'input','reply'))
        with self.db:
            self.db.execute("INSERT INTO memory_semantic_batches VALUES(1,?,'digest',?,0,'retired',0,0)",(SCOPE,model_json([semantic_batch.event_key(SCOPE,e) for e in ('old','also-old')])))
            semantic_batch.purge(self.db,1)
        self.assertEqual(self.db.execute('SELECT last_text,last_user_event FROM contacts WHERE scope=?',(SCOPE,)).fetchone(),('unattributed',None))


if __name__=='__main__':unittest.main()
