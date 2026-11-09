"""New Work ownership, partial licenses and v3 evidence; no network/model calls."""
import asyncio,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from bot_pipeline import BotPipeline
from context_protocol import model_json
from unified_context_20261108_test import message,request
from readonly_journal import ReadonlyJournal,ContextPrefixViolation
import summary_material as sm
import message_provenance as mp
from group_freshness import GroupFreshness

class Event:
 def __init__(self,identifier,scope='private:12345678',who='12345678'):
  self.extras={};self.identifier=identifier;self.scope=scope;self.who=who
  self.message_obj=NS(message_id=identifier,timestamp=time.time(),raw_message={'post_type':'message','message_type':'private','user_id':int(who),'self_id':99999999,'message_id':int(identifier),'message':[]})
 def get_extra(self,k,default=None):return self.extras.get(k,default)
 def set_extra(self,k,v):self.extras[k]=v
 def get_group_id(self):return self.scope[6:] if self.scope.startswith('group:') else ''
 def get_sender_id(self):return self.who
 def get_self_id(self):return '99999999'
 def get_platform_id(self):return 'bot'

class Ownership(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'b.sqlite');self.db=self.store.db;self.p=self.store.pipeline
  self.db.execute("CREATE TABLE fishbot_shared_context(id INTEGER PRIMARY KEY,state TEXT)");self.db.execute("INSERT INTO fishbot_shared_context VALUES(1,'active')");self.db.commit()
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def test_100_ingresses_have_one_work_and_exact_seen_set(self):
  a=Event('1');w=self.p.bind(a,{'botId':'bot'})
  for n in range(2,102):self.assertTrue(self.p.join_active(Event(str(n))))
  self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_events').fetchone()[0],1)
  self.assertEqual(self.p.unseen_inputs(w),100)
  self.p.seen_inputs(w,[message(w.scope,str(n)) for n in range(2,101)])
  self.assertEqual(self.p.unseen_inputs(w),1)
  w.status='silent';w.reason='model_no_reply_marker';self.p.finish(w,{});self.p.release_work(w)
  self.assertEqual(dict(self.db.execute('SELECT status,count(*) FROM pipeline_work_inputs GROUP BY status')),{ 'pending':1,'settled':99})
  self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_scope_work').fetchone()[0],0)
 def test_other_scope_parallel_and_duplicate_owner_rejected(self):
  w=self.p.bind(Event('1'),{'botId':'bot'})
  other=self.p.bind(Event('2','group:55555'),{'botId':'bot'})
  self.assertEqual(other.status,'received')
  contender=self.p.bind(Event('3'),{'botId':'bot'})
  self.assertEqual(contender.reason,'scope_work_owned_elsewhere')
  self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_scope_work').fetchone()[0],2)
 def test_duplicate_work_cannot_release_the_real_owner(self):
  w=self.p.bind(Event('1'),{'botId':'bot'});duplicate=self.p.bind(Event('1'),{'botId':'bot'})
  self.assertEqual(duplicate.status,'duplicate');self.p.release_work(duplicate);self.p.abandon_scope(duplicate)
  self.assertEqual(self.db.execute('SELECT event FROM pipeline_scope_work').fetchone()[0],w.event_id)
 def test_queued_input_can_rejoin_but_changed_evidence_cannot(self):
  w=self.p.bind(Event('1'),{'botId':'bot'});e=Event('2');self.p.join_active(e)
  self.db.execute("UPDATE pipeline_work_inputs SET status='queued',owner=NULL");self.db.commit()
  self.assertTrue(self.p.join_active(e));self.assertEqual(self.db.execute('SELECT status,owner FROM pipeline_work_inputs').fetchone(),('owned',w.event_id))
  e.message_obj.raw_message['message']=['changed']
  with self.assertRaisesRegex(ValueError,'redelivery_evidence_changed'):self.p.join_active(e)
 def test_failed_settlement_releases_owner_without_faking_success(self):
  w=self.p.bind(Event('1'),{'botId':'bot'});self.p.join_active(Event('2'));self.p.abandon_scope(w)
  self.assertEqual(self.db.execute('SELECT status FROM pipeline_work_inputs').fetchone()[0],'blocked_unknown')
  self.assertFalse(self.db.execute('SELECT 1 FROM pipeline_scope_work').fetchone())
 def test_terminal_redelivery_is_reconciled_without_a_model_or_effect(self):
  from initiative import resume_scope_input
  w=self.p.bind(Event('1'),{'botId':'bot'});self.p.join_active(Event('2'))
  w.status='silent';self.p.finish(w,{});self.p.release_work(w)
  self.db.execute("UPDATE pipeline_work_inputs SET status='queued'")
  self.db.execute("INSERT INTO pipeline_events(scope,subject,event,status,attempted,confirmed) VALUES('private:12345678','12345678','2','silent',0,0)");self.db.commit()
  self.assertFalse(asyncio.run(resume_scope_input(NS(),None,{},self.store,{})))
  self.assertEqual(self.db.execute('SELECT status,seen FROM pipeline_work_inputs').fetchone(),('settled_duplicate',0))
 def test_unknown_outcome_never_requeues_observed_ingress(self):
  w=self.p.bind(Event('1'),{'botId':'bot'});self.p.join_active(Event('2'))
  self.p.seen_inputs(w,[message(w.scope,'2')]);w.attempted=1
  self.p.finish(w,{});self.p.release_work(w)
  self.assertEqual(self.db.execute('SELECT status FROM pipeline_work_inputs').fetchone()[0],'blocked_unknown')
 def test_group_admission_wait_preserves_unseen_inputs_without_unknown_or_replay(self):
  scope='group:55555';w=self.p.bind(Event('1',scope),{'botId':'bot'})
  for n in range(2,102):self.p.join_active(Event(str(n),scope))
  w.stop('group_waiting','waiting');self.p.finish(w,{});self.p.release_work(w)
  self.assertEqual(dict(self.db.execute('SELECT status,count(*) FROM pipeline_work_inputs GROUP BY status')),{'deferred':100})
  self.assertEqual(self.db.execute('SELECT sum(seen),sum(retry_count) FROM pipeline_work_inputs').fetchone(),(0,0))
  from initiative import resume_scope_input
  self.assertFalse(asyncio.run(resume_scope_input(NS(),None,{},self.store,{})))
  e=Event('101',scope);e.set_extra('_hosted_deferred_group',{})
  e.set_extra('_hosted_deferred_group',{'event':'101'})
  resumed=self.p.bind(e,{'botId':'bot'})
  self.assertTrue(resumed.ledger_owner);self.assertTrue(resumed.scope_owner)
  self.assertTrue(resumed.observes_input);self.assertEqual(resumed.work_id,w.work_id)
  self.assertEqual(self.p.unseen_inputs(resumed),99)
  # Only the exact events with a semantic no-response decision are settled.
  self.p.ignored_inputs(resumed,['100','101'])
  resumed.stop('group_model_silent_or_stale','silent');self.p.finish(resumed,{})
  self.p.release_work(resumed)
  self.assertEqual(dict(self.db.execute('SELECT status,count(*) FROM pipeline_work_inputs GROUP BY status')),{'deferred':98,'settled':2})
 def test_deferred_admission_cannot_replay_confirmed_or_unknown_output(self):
  for index,status,attempted,confirmed in ((1,'sent',1,1),(2,'unknown',1,0),(3,'received',0,0)):
   self.db.execute('INSERT INTO pipeline_events(scope,subject,event,status,attempted,confirmed) VALUES(?,?,?,?,?,?)',('group:55555','12345678',str(index),status,attempted,confirmed));self.db.commit()
   e=Event(str(index),'group:55555');e.set_extra('_hosted_deferred_group',{'event':str(index)})
   w=self.p.bind(e,{'botId':'bot'});self.assertEqual(w.status,'duplicate');self.assertFalse(w.scope_owner)
 def test_restart_repairs_only_exact_zero_effect_waiting_root(self):
  scope='group:55555'
  for e,status,reason,attempted in (('1','waiting','group_waiting',0),('2','waiting','group_waiting',1),('3','silent','group_model_silent_or_stale',0)):
   self.db.execute('INSERT INTO pipeline_events(scope,subject,event,status,reason,attempted,confirmed) VALUES(?,?,?,?,?,?,0)',(scope,'12345678',e,status,reason,attempted))
   self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,status,seen) VALUES(?,?,?,'blocked_unknown',0)",(scope,e,'12345678'))
  self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,status,seen) VALUES(?, '4','12345678','blocked_unknown',0)",(scope,));self.db.commit()
  BotPipeline(self.db)
  self.assertEqual(list(self.db.execute('SELECT event,status,seen FROM pipeline_work_inputs ORDER BY event')),[('1','deferred',0),('2','blocked_unknown',0),('3','blocked_unknown',0),('4','blocked_unknown',0)])
 def test_deferred_inbox_cannot_replay_settled_or_held_joined_input_without_root_ledger(self):
  for n,status in enumerate(('settled','settled_duplicate','blocked_unknown'),10):
   with self.subTest(status=status):
    identifier=str(n)
    self.db.execute('INSERT INTO pipeline_work_inputs(scope,event,subject,status,seen) VALUES(?,?,?,?,0)',('group:55555',identifier,'12345678',status));self.db.commit()
    e=Event(identifier,'group:55555');e.set_extra('_hosted_deferred_group',{'event':identifier})
    w=self.p.bind(e,{'botId':'bot'})
    self.assertEqual(w.status,'duplicate');self.assertFalse(w.scope_owner);self.assertFalse(w.ledger_owner)
    self.assertIsNone(self.db.execute('SELECT event FROM pipeline_events WHERE event=?',(identifier,)).fetchone())
 def test_exact_terminal_ledger_repair_does_not_guess_waiting_or_owner(self):
  w=self.p.bind(Event('1'),{'botId':'bot'});mp.bind_model_input(self.db,'bot_shared',w.scope,'1',w.subject,message(w.scope,'1'))
  w.status='silent';self.p.finish(w,{})
  key=model_json([w.scope,'1']);row=json.loads(self.db.execute("SELECT data FROM memory_message_sources WHERE event=? AND direction='model'",(key,)).fetchone()[0]);row.pop('settlement',None)
  self.db.execute("UPDATE memory_message_sources SET data=? WHERE event=? AND direction='model'",(model_json(row),key))
  self.assertEqual(mp.recover_model_settlements(self.db),1)
  self.assertEqual(mp.model_input_settlement(self.db,'bot_shared',key)['status'],'silent')
  self.assertEqual(mp.model_output_owners(self.db,'bot_shared',[message(w.scope,'1'),{'role':'assistant','content':'unbound'}]).get((key,1)),None)

class PartialCheckpoint(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'b.sqlite');self.db=self.store.db;self.r=self.store.reflection
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def proof(self,m):
  scope='private:12345678'
  from context_protocol import source
  o=source(m);mp.settle_model_output(self.db,NS(scope=scope,event_id=o['event_id'],subject=o['sender_id'],status='silent',attempted=0,confirmed=0,planned_messages=0,confirmed_messages=0))
  return sm.snapshots([m],'bot_shared',db=self.db)[0]
 def seed(self):
  sid=self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('bot_shared','covered summary','[]',?)",(time.time(),)).lastrowid
  self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(sid,))
  self.db.execute("INSERT INTO memory_hold_rounds VALUES('bot_shared','unresolved',NULL,'uncovered',?)",(time.time(),))
  self.db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id,pending_events) VALUES('bot_shared',35,35,0,'pending_coverage',?,?,?)",(time.time(),sid,'["unresolved"]'))
  return sid
 def test_partial_permit_requires_this_product_v3_not_any_old_coverage(self):
  sid=self.seed();m=message('private:12345678','a');mat=self.proof(m)
  self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',('bot_shared',mat['event'],mat['subject'],sm.coverage_digest(mat),sid))
  self.r._promote_pending_checkpoints('bot_shared');self.db.commit()
  permit=self.r.reserve_context_reconstruction('bot_shared','request',[sid])
  self.assertTrue(permit['partialRetirement'])
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],1)
  detail=json.loads(self.db.execute("SELECT detail FROM memory_checkpoint_issues WHERE kind='coverage'").fetchone()[0]);self.assertEqual(detail['hold_count'],1)
  self.r.settle_context_reconstruction(permit,True)
  self.r._promote_pending_checkpoints('bot_shared');self.db.commit()
  self.assertIsNone(self.r.reserve_context_reconstruction('bot_shared','again',[sid]))
 def test_legacy_digest_stays_pending_and_exception_is_deduplicated(self):
  sid=self.seed();m=message('private:12345678','a');mat=sm.snapshots([m],'bot_shared')[0]
  self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',('bot_shared',mat['event'],mat['subject'],sm.coverage_digest(mat),sid))
  for _ in range(3):self.r._promote_pending_checkpoints('bot_shared')
  self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'pending_coverage')
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_checkpoint_issues').fetchone()[0],1)
 def test_projection_keeps_uncovered_media_and_exact_system(self):
  a=message('private:12345678','a');b=message('private:12345678','b');c=message('private:12345678','c')
  mat=self.proof(a);sid=self.seed();self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',('bot_shared',mat['event'],mat['subject'],sm.coverage_digest(mat),sid))
  original=[{'role':'system','content':'fixed'},a,b,c];result=sm.project_history(original,sm.coverage(self.db,'bot_shared'),'bot_shared','c')
  self.assertEqual(result,[original[0],b,c]);self.assertEqual(model_json(result[1]),model_json(b))
 def test_journal_partial_stage_rejects_missing_hold_then_restarts_exactly(self):
  a=message('private:12345678','a');b=message('private:12345678','b');c=message('group:55555','c')
  b['content']=[{'type':'text','text':b['content']},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]
  j=ReadonlyJournal(self.db,'bot_shared')
  p=j.prepare(request(a)+[b,c],('bot',),strict=True,reconstruction_scope='bot_shared')
  self.assertTrue(j.stage_candidate(p));self.assertTrue(j.commit(p));original=model_json(j.last.wire)
  mat=self.proof(a);sid=self.seed()
  self.db.execute('INSERT INTO memory_summary_coverage VALUES(?,?,?,?,?)',('bot_shared',mat['event'],mat['subject'],sm.coverage_digest(mat),sid))
  self.r._promote_pending_checkpoints('bot_shared');self.db.commit()
  with self.assertRaisesRegex(ContextPrefixViolation,'retained_material_missing'):
   j.prepare(request(c),('bot',),strict=True,reconstruction_scope='bot_shared',reconstruction_authority=self.r,retirement='checkpoint')
  self.assertEqual(model_json(j.last.wire),original)
  self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'available')
  p=j.prepare(request(b)+[c],('bot',),strict=True,reconstruction_scope='bot_shared',reconstruction_authority=self.r,retirement='checkpoint')
  self.assertTrue(j.stage_candidate(p));self.assertTrue(j.commit(p))
  self.assertEqual(j.last.base,[request(a)[0],b,c])
  self.assertEqual(ReadonlyJournal(self.db,'bot_shared').last.wire,j.last.wire)
  self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints').fetchone()[0],'consumed')
  row=self.db.execute('SELECT retired_events,retained_events FROM memory_checkpoint_retirements').fetchone()
  self.assertEqual(json.loads(row[0]),[model_json(['private:12345678','a'])]);self.assertEqual(len(json.loads(row[1])),2)

class SeenFreshness(unittest.IsolatedAsyncioTestCase):
 async def test_seen_event_skips_but_real_new_event_and_state_change_call(self):
  f=GroupFreshness();start=time.monotonic();calls=[]
  f.observe('private:12345678','2','12345678','new')
  async def call(payload):calls.append(payload);return '{"action":"keep"}'
  args=('private:12345678',0,start,'1','draft','',call,lambda:True)
  d=await f.check(*args,seen_events={'2'});self.assertFalse(d['checked']);self.assertEqual(calls,[])
  d=await f.check(*args,seen_events=set());self.assertTrue(d['checked'])
  d=await f.check(*args,seen_events={'2'},state_changed=True);self.assertTrue(d['checked']);self.assertEqual(len(calls),2)

if __name__=='__main__':unittest.main()
