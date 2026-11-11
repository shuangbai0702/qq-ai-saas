"""Permanent Work identity and activation/input decisions; offline evidence only."""
import ast,json,sys,tempfile,time,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from context_protocol import model_json
from group_freshness import GroupFreshness
from work_hold_contract_20261109_test import Event
from unified_context_20261108_test import message
import message_provenance as mp


class PermanentWork(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'b.sqlite'
  self.store=BehaviorStore(self.path);self.db=self.store.db;self.p=self.store.pipeline
  self.db.execute('CREATE TABLE fishbot_shared_context(id INTEGER PRIMARY KEY,state TEXT,group_limit INTEGER)')
  self.db.execute("INSERT INTO fishbot_shared_context VALUES(1,'active',2)");self.db.commit()
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def bind(self,event='1',scope='private:12345678',tenant='tenant-A',bot='bot'):
  return self.p.bind(Event(event,scope),{'tenantId':tenant,'botId':bot})
 def done(self,w,status='silent',reason='model_no_reply_marker'):
  w.status=status;w.reason=reason;self.p.finish(w,{});self.p.release_work(w)
 def joined(self):
  w=self.bind();self.assertTrue(self.p.join_active(Event('2')))
  mp.bind_model_input(self.db,'bot_shared',w.scope,'2',w.subject,message(w.scope,'2'))
  return w
 def input(self):return self.db.execute("SELECT seen,status,owner,payload FROM pipeline_work_inputs WHERE event='2'").fetchone()
 def test_logical_identity_survives_event_and_restart_but_isolates_tenant_bot_scope(self):
  first=self.bind();stable=first.work_id;self.done(first)
  second=self.bind('2');self.assertEqual(second.work_id,stable);self.assertNotEqual(first.event_id,second.event_id)
  self.done(second);self.store.close()
  self.store=BehaviorStore(self.path);self.db=self.store.db;self.p=self.store.pipeline
  third=self.bind('3');self.assertEqual(third.work_id,stable);self.done(third)
  for n,changes in enumerate([{'scope':'group:55555'},{'tenant':'tenant-B'},{'bot':'bot-B'}],4):
   with self.subTest(changes=changes):
    w=self.bind(str(n),**changes);self.assertNotEqual(w.work_id,stable);self.done(w)
 def test_each_activation_persists_its_own_passes_and_exit_reason(self):
  a=self.bind();self.p.activation_progress(a,4,'activation_pass_limit');self.done(a)
  b=self.bind('2');self.p.activation_progress(b,1,'inputs_decided');self.done(b)
  rows=self.db.execute('SELECT event,work_id,passes,exit_reason FROM pipeline_events ORDER BY event').fetchall()
  self.assertEqual(rows,[('1',a.work_id,4,'activation_pass_limit'),('2',a.work_id,1,'inputs_decided')])
 def test_joined_at_survives_parent_ambient_admission_wait(self):
  w=self.bind('101',scope='group:55555')
  e=Event('102','group:55555');e.message_obj.raw_message.update(message_type='group',group_id=55555,message=[{'type':'at','data':{'qq':'99999999'}}])
  self.assertTrue(self.p.persist_input(e,{'tenantId':'tenant-A','botId':'bot','generation':1,'revision':1}))
  self.assertTrue(self.p.join_active(e))
  w.stage('admission');w.stop('group_waiting','waiting');self.p.finish(w,{});self.p.release_work(w)
  self.assertEqual(self.db.execute("SELECT status,owner FROM pipeline_work_inputs WHERE event='102'").fetchone(),('pending',None))
  self.assertIsNotNone(self.db.execute("SELECT payload FROM pipeline_work_inputs WHERE event='102'").fetchone()[0])
 def test_unrelated_at_does_not_promote_ambient_wait(self):
  w=self.bind('201',scope='group:55555')
  e=Event('202','group:55555');e.message_obj.raw_message.update(message_type='group',group_id=55555,message=[{'type':'at','data':{'qq':'88888888'}}])
  self.assertTrue(self.p.persist_input(e,{'tenantId':'tenant-A','botId':'bot','generation':1,'revision':1}))
  self.assertTrue(self.p.join_active(e))
  w.stage('admission');w.stop('group_waiting','waiting');self.p.finish(w,{});self.p.release_work(w)
  self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='202'").fetchone()[0],'deferred')
 def test_legacy_empty_index_cannot_force_live_work_to_replan(self):
  scope='private:12345678'
  self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,payload,status,created) VALUES(?,?,?,NULL,'pending',?)",(scope,'old','12345678',time.time()-86400))
  self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,payload,status,created) VALUES(?,?,?,?,'pending',?)",('group:55555','other','12345678','{}',time.time()))
  w=self.bind('101')
  self.assertEqual(self.p.unseen_inputs(w),0)
  self.assertEqual(self.db.execute("SELECT status,owner,payload FROM pipeline_work_inputs WHERE event='old'").fetchone(),('blocked_recovery',None,None))
  self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='other'").fetchone()[0],'pending')
 def test_seen_superseded_draft_leaves_question_pending(self):
  w=self.joined();self.p.seen_inputs(w,[message(w.scope,'2')]);self.done(w,'superseded','freshness_drop')
  seen,status,owner,payload=self.input()
  self.assertEqual((seen,status,owner),(1,'pending',w.event_id));self.assertIsNotNone(payload)
  self.assertIsNone(mp.model_input_settlement(self.db,'bot_shared',model_json([w.scope,'2'])))
  self.assertIsNone(self.db.execute('SELECT event FROM pipeline_scope_work WHERE scope=?',(w.scope,)).fetchone())
  next_work=self.bind('3')
  self.assertTrue(next_work.scope_owner)
  self.assertEqual(self.input()[:3],(0,'owned',next_work.event_id))
 def test_seen_fully_confirmed_send_settles_joined_input(self):
  w=self.joined();self.p.seen_inputs(w,[message(w.scope,'2')]);w.planned_messages=2
  for _ in range(2):self.p.sending(w);self.p.confirmed(w);w.confirmed_messages+=1
  self.p.finish(w,{});self.p.release_work(w)
  self.assertEqual(w.status,'sent');self.assertEqual(self.input()[:3],(1,'settled',w.event_id))
  self.assertIsNone(self.input()[3])
  settlement=mp.model_input_settlement(self.db,'bot_shared',model_json([w.scope,'2']))
  self.assertEqual(settlement['basis'],'main_completed')
 def test_seen_no_reply_requires_explicit_model_decision(self):
  w=self.joined();self.p.seen_inputs(w,[message(w.scope,'2')]);self.done(w,reason='no_output')
  self.assertEqual(self.input()[1],'pending')
  next_work=self.bind('3');self.p.seen_inputs(next_work,[message(next_work.scope,'2')])
  self.done(next_work,reason='model_no_reply_marker')
  self.assertEqual(self.input()[:3],(1,'settled',next_work.event_id))
 def test_ignored_is_explicit_no_response_not_coverage(self):
  w=self.joined();self.p.ignored_inputs(w,['2']);self.assertEqual(self.input()[0],3)
  self.done(w,'superseded','freshness_drop')
  self.assertEqual(self.input()[:3],(3,'settled',w.event_id));self.assertIsNotNone(self.input()[3])
  settlement=mp.model_input_settlement(self.db,'bot_shared',model_json([w.scope,'2']))
  self.assertEqual(settlement['basis'],'explicit_no_response');self.assertEqual(settlement['confirmed'],0)
 def test_incomplete_plan_cannot_settle_seen_question(self):
  w=self.joined();self.p.seen_inputs(w,[message(w.scope,'2')]);w.planned_messages=2
  self.p.sending(w);self.p.confirmed(w);w.confirmed_messages=1
  self.p.finish(w,{});self.p.release_work(w)
  self.assertEqual(w.status,'partial');self.assertEqual(self.input()[1],'blocked_unknown')
 def test_activation_loop_keeps_four_pass_limit(self):
  tree=ast.parse((ROOT/'adapters/astrbot/behavior.py').read_text(encoding='utf8'))
  guard=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='guarded_execute')
  limits=[n for n in ast.walk(guard) if isinstance(n,ast.Compare) and isinstance(n.left,ast.Name) and n.left.id=='passes'
          and len(n.ops)==1 and isinstance(n.ops[0],ast.GtE) and isinstance(n.comparators[0],ast.Constant) and n.comparators[0].value==4]
  self.assertEqual(len(limits),1)
  self.assertTrue(any(isinstance(n,ast.Constant) and n.value=='activation_pass_limit' for n in ast.walk(guard)))


class FreshnessDisposition(unittest.IsolatedAsyncioTestCase):
 async def decide(self,reply):
  f=GroupFreshness();start=time.monotonic()
  f.observe('group:55555','2','12345678','question');f.observe('group:55555','3','87654321','feedback')
  async def call(payload):
   self.assertEqual({r['event'] for r in payload['latest_messages']},{'2','3'})
   return json.dumps(reply)
  return await f.check('group:55555',0,start,'1','draft','',call,lambda:True)
 async def test_valid_known_dispositions_are_distinct_and_deduplicated(self):
  d=await self.decide({'action':'keep','responded_events':['2','2'],'ignored_events':['3','3']})
  self.assertEqual(d['responded_events'],['2']);self.assertEqual(d['ignored_events'],['3'])
  d=await self.decide({'action':'keep','responded_events':[],'ignored_events':[]})
  self.assertEqual(d['responded_events'],[]);self.assertEqual(d['ignored_events'],[])
 async def test_overlap_unknown_ids_and_invalid_types_are_rejected(self):
  for covered,ignored in [(['2'],['2']),(['missing'],[]),([],['missing']),('2',[]),([],['3',7])]:
   with self.subTest(covered=covered,ignored=ignored):
    d=await self.decide({'action':'keep','responded_events':covered,'ignored_events':ignored})
    self.assertEqual(d['action'],'drop');self.assertEqual(d['reason'],'review_unavailable')
    self.assertEqual(d.get('responded_events',[]),[]);self.assertEqual(d.get('ignored_events',[]),[])


if __name__=='__main__':unittest.main()
