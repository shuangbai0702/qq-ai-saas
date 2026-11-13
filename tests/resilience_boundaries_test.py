"""Fault boundaries using the real SQLite journals and pipeline schema."""
import ast,asyncio,hashlib,json,os,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from readonly_journal import ReadonlyJournal,Prepared
from bot_pipeline import BotPipeline
from input_reconciliation import reconcile
from model_request_capture import prune_captures
from context_protocol import model_json,scoped_event_hash
import external_action_budget as budget

class Boundaries(unittest.TestCase):
    def test_overflow_allows_existing_prefix_but_never_forgets_missing_scope(self):
        with sqlite3.connect(':memory:') as db:
            journal=ReadonlyJournal(db,'known')
            msg=[{'role':'system','content':'固定'},{'role':'user','content':'first'}]
            p=Prepared(1,None,msg,msg,{},1,time.time(),'initial')
            self.assertTrue(journal._persist(p))
            db.execute('UPDATE fishbot_readonly_journal_meta SET overflow=1');db.commit()
            p.base=p.wire=msg+[{'role':'assistant','content':'append'}]
            self.assertTrue(journal._persist(p))
            self.assertEqual(ReadonlyJournal(db,'known').last.wire,p.wire)
            self.assertEqual(ReadonlyJournal(db,'unknown').recovery_error,'journal_snapshot_missing')
            self.assertEqual(db.execute('SELECT overflow FROM fishbot_readonly_journal_meta').fetchone()[0],1)

    def test_recovery_requires_terminal_exact_identity_no_active_owner_and_full_receipts(self):
        with sqlite3.connect(':memory:') as db:
            BotPipeline(db)
            for i,(subject,status,attempted,confirmed) in enumerate([
                ('person','sent',1,1),('person','sent',1,0),('person','unknown',0,0),('wrong','silent',0,0),('person','sent',1,1)]):
                scope='private:'+str(i)
                db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,status) VALUES(?,'event','person','blocked_recovery')",(scope,))
                db.execute("INSERT INTO pipeline_events(scope,event,subject,status,attempted,confirmed,finished_at) VALUES(?,'event',?,?,?,?,1)",(scope,subject,status,attempted,confirmed))
            db.execute("INSERT INTO pipeline_scope_work(scope,event) VALUES('private:4','active')");db.commit()
            reconcile(db,NS())
            self.assertEqual(dict(db.execute('SELECT scope,status FROM pipeline_work_inputs')),{'private:0':'settled_proven',**{'private:'+str(i):'blocked_recovery' for i in range(1,5)}})
            self.assertEqual(db.execute('SELECT count(*) FROM pipeline_work_inputs').fetchone()[0],5)

    def test_capture_retention_never_removes_unrecognized_or_recent_material(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'model-request-captures';now=time.time()
            for name,filename,age in [('a'*24,'0.request.json',90000),('b'*24,'0.response.json',1),('c'*24,'journal.json',90000)]:
                p=folder/name/filename;p.parent.mkdir(parents=True);p.write_text('private');os.utime(p,(now-age,now-age))
            prune_captures(root,now)
            self.assertFalse((folder/('a'*24)).exists())
            self.assertTrue((folder/('b'*24)/'0.response.json').exists())
            self.assertTrue((folder/('c'*24)/'journal.json').exists())

    def test_event_hash_preserves_previous_bytes(self):
        for scope,event in [('群:你好','001'),('private:123',123),('a"\\','x\n')]:
            previous=hashlib.sha256(model_json([scope,str(event)]).encode()).hexdigest()
            self.assertEqual(scoped_event_hash(scope,event),previous)

    def test_issue_aggregation_preserves_authority_and_missing_evidence(self):
        from memory_reflection import Reflection
        with sqlite3.connect(':memory:') as db:
            BotPipeline(db)
            db.execute('CREATE TABLE memory_checkpoint_issues(scope TEXT,checkpoint_id INTEGER,kind TEXT,status TEXT,detail TEXT,first_seen REAL,updated REAL,PRIMARY KEY(scope,checkpoint_id,kind))')
            reflection=Reflection.__new__(Reflection);reflection.db=db
            for event in ('one','two'):
                db.execute("INSERT INTO pipeline_work_inputs(scope,event,status) VALUES('private:1',?,'blocked_unknown')",(event,))
            for event in ('one','two','missing'):
                db.execute("INSERT INTO memory_checkpoint_issues VALUES('bot_shared',0,?,'open',?,1,1)",('input_recovery:private:1:'+event,json.dumps({'scope':'private:1','event':event})))
            db.commit();reconcile(db,reflection)
            rows=dict(db.execute('SELECT kind,status FROM memory_checkpoint_issues'))
            self.assertEqual(rows['input_recovery:private:1'],'open')
            self.assertEqual(rows['input_recovery:private:1:one'],'resolved')
            self.assertEqual(rows['input_recovery:private:1:missing'],'open')
            detail=json.loads(db.execute("SELECT detail FROM memory_checkpoint_issues WHERE kind='input_recovery:private:1'").fetchone()[0])
            self.assertEqual(detail['blocked'],{'blocked_unknown':2})
            self.assertEqual(db.execute('SELECT count(*) FROM pipeline_work_inputs').fetchone()[0],2)

class Cancellation(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_waits_owned_receipt_then_propagates(self):
        entered=asyncio.Event();release=asyncio.Event();settled=[]
        async def update(*args):
            entered.set();await release.wait();settled.append(args[-1])
        with patch.object(budget,'update',update):
            task=asyncio.create_task(budget.settle({}, {},'logical','confirmed'))
            await entered.wait();task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
            self.assertFalse(task.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(settled,['confirmed'])

    async def test_actual_member_unload_revokes_first_and_runs_cleanup_after_failure(self):
        # Compile the real nested function without starting a production worker.
        source=Path(__file__).resolve().parents[1]/'adapters/astrbot/shared-worker.py'
        node=next(n for n in ast.walk(ast.parse(source.read_text(encoding='utf8'))) if isinstance(n,ast.AsyncFunctionDef) and n.name=='unload')
        calls=[];settings={'bot':{}};members={'bot':{'generation':1,'revision':2,'confId':'conf'}};stores={'bot':NS()}
        async def drain(id):
            self.assertNotIn(id,settings);calls.append('drain');raise RuntimeError('controlled drain failure')
        async def cleanup(*args):calls.append(args[0])
        core=NS(event_bus=NS(stop_platform=cleanup),platform_manager=NS(terminate_platform=cleanup),provider_manager=NS(terminate_provider=cleanup,providers_config=[]),umop_config_router=NS(delete_route=cleanup),astrbot_config_mgr=NS(delete_conf=cleanup),astrbot_config={'platform':[]})
        namespace={'asyncio':asyncio,'behavior_settings':settings,'members':members,'tombstones':{},'core':core,'behavior':{'drain':drain,'stores':stores,'release':lambda id:calls.append('release')},'worker_lifecycle':NS(record=lambda *a,**k:None),'db_helper':NS(delete_persona=cleanup)}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
        with self.assertRaisesRegex(RuntimeError,'controlled drain failure'):await namespace['unload']('bot')
        self.assertNotIn('bot',members);self.assertIn('provider-bot',calls)
        self.assertIn('conf',calls);self.assertNotIn('release',calls)
        self.assertIn('bot',stores) # Unproven drainage retains the resource owner.

if __name__=='__main__':unittest.main()
