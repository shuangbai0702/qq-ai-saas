"""Incident-cohort archival policy and non-round media; no API or QQ effects."""
import asyncio,contextvars,copy,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parent)]
from behavior import BehaviorStore
from unified_context_20261108_test import message,request
from context_protocol import source,model_json,SOURCE
from readonly_journal import ReadonlyJournal
import summary_material as sm
import message_provenance as mp
import journal_storage

class Materials(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'b.sqlite');self.db=self.store.db;self.r=self.store.reflection
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def old(self,event='a',image=False):
  m=message('private:12345678',event);o=source(m);o['at']=sm.LEGACY_CUTOFF-3600
  m['content']=SOURCE+model_json(o)+'\n真实原始图片'
  if image:m['content']=[{'type':'text','text':m['content']},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]
  return m
 def seed(self,m):
  mat=sm.snapshots([m],'bot_shared',db=self.db)[0]
  sid=self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('bot_shared','已整理的历史图片文字语义','[]',?)",(time.time(),)).lastrowid
  self.db.execute('INSERT INTO memory_summary_unresolved VALUES(?,?,?,?)',('bot_shared',mat['event'],sid,mat['digest']))
  self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(sid,))
  self.db.execute("INSERT INTO memory_hold_rounds VALUES('bot_shared',?,NULL,'uncovered',?)",(mat['event'],sm.LEGACY_CUTOFF-100))
  cp=self.db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id,pending_events) VALUES('bot_shared',35,35,0,'pending_coverage',?,?,?)",(time.time(),sid,model_json([mat['event']]))).lastrowid
  return mat,sid,cp
 def test_exact_round_identity_repair_does_not_create_rounds(self):
  import contextvars
  self.r.record('bot_shared','12345678','existing','question','confirmed answer',1000)
  actual=self.db.execute('SELECT id FROM memory_rounds').fetchone()[0]
  self.db.execute("INSERT INTO memory_hold_rounds VALUES('bot_shared','existing',NULL,'uncovered',1000)")
  self.db.execute("INSERT INTO memory_hold_rounds VALUES('bot_shared','missing',NULL,'uncovered',1000)");self.db.commit()
  self.r.binding=({'memorySummary':True},NS(text_chat=lambda **kw:None),lambda:True,{},contextvars.ContextVar('event',default=None))
  self.r.retry()
  self.assertEqual(self.db.execute("SELECT round_id FROM memory_hold_rounds WHERE event='existing'").fetchone()[0],actual)
  self.assertIsNone(self.db.execute("SELECT round_id FROM memory_hold_rounds WHERE event='missing'").fetchone()[0])
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],1)
 def test_nonround_image_is_pending_not_a_business_round_and_anchor_is_required(self):
  a=self.old(image=True);b=self.old('b');c=self.old('c',image=True);original=copy.deepcopy([a,b,c])
  key=lambda m:model_json([source(m)['scope'],source(m)['event_id']])
  self.assertEqual(sm.register_nonround_images(self.db,'bot_shared',[a,b,c],['absent']),0)
  self.assertEqual(sm.register_nonround_images(self.db,'bot_shared',[a,b,c],[key(b)]),1)
  self.assertEqual(sm.register_nonround_images(self.db,'bot_shared',[a,b,c],[key(b)]),0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT event FROM memory_pending').fetchone()[0],key(a))
  self.assertEqual(json.loads(self.db.execute('SELECT material FROM memory_round_material').fetchone()[0])['images'],['data:image/png;base64,YQ=='])
  self.assertEqual([a,b,c],original)
 def test_policy_is_not_coverage_or_a_new_permit_and_known_unknown_stays_protected(self):
  m=self.old();mat,sid,cp=self.seed(m)
  self.assertFalse(sm.coverage_eligible(mat,'bot_shared'));self.assertTrue(sm.legacy_policy_eligible(mat))
  bad=copy.deepcopy(mat);bad['input_settlement']={'status':'waiting','attempted':1,'confirmed':0,'planned':1,'confirmed_messages':0}
  self.assertFalse(sm.legacy_policy_eligible(bad))
  self.assertEqual(sm.offer_legacy_retirements(self.db,'bot_shared',{mat['event']:bad},[mat['event']],sid,cp),0)
  self.assertEqual(sm.offer_legacy_retirements(self.db,'bot_shared',{mat['event']:mat},[mat['event']],sid,cp),1)
  proof=sm.coverage(self.db,'bot_shared')[mat['event']];self.assertEqual(proof['version'],4)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_coverage').fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],1)
  self.r._promote_pending_checkpoints('bot_shared');self.db.commit()
  permit=self.r.reserve_context_reconstruction('bot_shared','test',[sid]);self.assertTrue(permit['partialRetirement'])
  self.r.settle_context_reconstruction(permit,True)
  self.assertIsNone(self.r.reserve_context_reconstruction('bot_shared','again',[sid]))
 def test_late_execution_evidence_cannot_replace_media_retirement_choice(self):
  m=self.old(image=True);mat,sid,cp=self.seed(m)
  self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',('bot_shared',mat['subject'],mat['event'],mat['text'],'',1000))
  sm.remember(self.db,'bot_shared',[mat]);self.db.commit()
  def refresh(scope,events):
   o=source(m);mp.settle_model_output(self.db,NS(scope=o['scope'],event_id=o['event_id'],subject=o['sender_id'],status='silent',attempted=0,confirmed=0,planned_messages=0,confirmed_messages=0))
   sm.remember(self.db,scope,sm.snapshots([m],scope,db=self.db))
  self.r.recapture=refresh
  self.assertEqual(self.r.recover_proven_coverage('bot_shared'),0)
  self.assertEqual(sm.coverage(self.db,'bot_shared'),{})
  self.db.execute('INSERT INTO memory_media_retirement_choices VALUES(?,?,?,?,1)',('bot_shared',mat['event'],mat['digest'],sid))
  self.assertEqual(self.r.recover_proven_coverage('bot_shared'),1)
  self.assertTrue(sm.media_retirement_allowed(self.db,'bot_shared',mat['event'],mat))
  self.db.execute('DELETE FROM memory_summaries WHERE id=?',(sid,))
  self.assertFalse(sm.media_retirement_allowed(self.db,'bot_shared',mat['event'],mat))
 def test_invalidated_policy_can_be_offered_at_a_new_real_checkpoint(self):
  m=self.old();mat,sid,cp=self.seed(m)
  self.assertEqual(sm.offer_legacy_retirements(self.db,'bot_shared',{mat['event']:mat},[mat['event']],sid,cp),1)
  self.db.execute("UPDATE memory_reconstruction_checkpoints SET state='invalidated' WHERE id=?",(cp,))
  cp2=self.db.execute("INSERT INTO memory_reconstruction_checkpoints(scope,through_round_id,round_count,epoch,state,created,summary_id,pending_events) VALUES('bot_shared',70,35,0,'pending_coverage',?,?,?)",(time.time(),sid,model_json([mat['event']]))).lastrowid
  self.assertEqual(sm.offer_legacy_retirements(self.db,'bot_shared',{mat['event']:mat},[mat['event']],sid,cp2),1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_retirement_exceptions').fetchone()[0],2)
  self.assertEqual(sm.coverage(self.db,'bot_shared')[mat['event']]['policy_checkpoint'],cp2)
 def test_full_permit_cannot_retire_a_future_policy_block(self):
  from readonly_journal import ContextPrefixViolation
  a=self.old();b=self.old('b');j=ReadonlyJournal(self.db,'bot_shared')
  p=j.prepare(request(a)+[b],('bot',),strict=True,reconstruction_scope='bot_shared');self.assertTrue(j.stage_candidate(p));self.assertTrue(j.commit(p))
  mat,sid,cp=self.seed(a);sm.offer_legacy_retirements(self.db,'bot_shared',{mat['event']:mat},[mat['event']],sid,cp)
  self.db.commit()
  oldpermit={'kind':'summary_checkpoint','id':cp-1,'partialRetirement':False}
  with self.assertRaises(ContextPrefixViolation):j._validate_partial_retirement(oldpermit,self.r,'bot_shared',j.last,request(b))
 def test_legal_retirement_archives_exact_media_and_removes_hold_only_on_durable_stage(self):
  a=self.old(image=True);b=self.old('b');j=ReadonlyJournal(self.db,'bot_shared')
  p=j.prepare(request(a)+[b],('bot',),strict=True,reconstruction_scope='bot_shared');self.assertTrue(j.stage_candidate(p));self.assertTrue(j.commit(p))
  before=copy.deepcopy(j.last.wire)
  mat,sid,cp=self.seed(a);sm.offer_legacy_retirements(self.db,'bot_shared',{mat['event']:mat},[mat['event']],sid,cp)
  self.r._promote_pending_checkpoints('bot_shared');self.db.commit()
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],1)
  p=j.prepare(request(b),('bot',),strict=True,reconstruction_scope='bot_shared',reconstruction_authority=self.r,retirement='checkpoint')
  self.assertEqual(j.last.wire,before)
  with patch('readonly_journal.MAX_CHECKPOINT_ARCHIVE_BYTES',0):
   self.assertFalse(j.stage_candidate(p))
  self.assertEqual(ReadonlyJournal(self.db,'bot_shared').last.wire,before)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],1)
  self.assertEqual(self.db.execute('SELECT state FROM memory_reconstruction_checkpoints WHERE id=?',(cp,)).fetchone()[0],'reserved')
  self.assertTrue(j.stage_candidate(p));self.assertTrue(j.commit(p))
  self.assertEqual(self.db.execute('SELECT state FROM memory_retirement_exceptions').fetchone()[0],'retired')
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_hold_rounds').fetchone()[0],0)
  row=self.db.execute('SELECT version,payload,sha FROM memory_checkpoint_archives WHERE checkpoint_id=?',(cp,)).fetchone()
  archived=journal_storage.decode(row[1],j.scope_key,lambda scope,sha:bytes(self.db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(scope,sha)).fetchone()[0]))
  self.assertEqual(archived['wire'],before)
  self.assertEqual(ReadonlyJournal(self.db,'bot_shared').last.wire,j.last.wire)

class Cancellation(unittest.IsolatedAsyncioTestCase):
 async def test_close_persists_cancellation_before_database_closes(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'b.sqlite';store=BehaviorStore(path);r=store.reflection;entered=asyncio.Event()
   async def chat(**kwargs):
    entered.set();await asyncio.Event().wait()
   for i in range(35):r.record('bot_shared','12345678',str(i),'真实输入','已确认回复')
   r.maybe_start('bot_shared',{'memory':True,'memorySummary':True},NS(text_chat=chat),lambda:True,{},contextvars.ContextVar('close_test',default=None))
   task=r.task
   try:
    await asyncio.wait_for(entered.wait(),2)
    store.close()
    with self.assertRaises(asyncio.CancelledError):await task
    db=sqlite3.connect(path)
    try:
     issue=db.execute("SELECT detail FROM memory_checkpoint_issues WHERE kind='canceled_summary'").fetchone()
     self.assertEqual(json.loads(issue[0])['source'],'reflection_close')
     self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
     self.assertEqual(db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints').fetchone()[0],0)
    finally:db.close()
   finally:
    if not r.closed:store.close()

if __name__=='__main__':unittest.main()
