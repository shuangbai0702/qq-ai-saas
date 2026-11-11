"""Addressed joined-input recovery stays inside the existing Work ledger."""
import asyncio
import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.message.components import At,Reply
from bot_pipeline import BotPipeline
from unified_context_20261108_test import message as model_message
import message_provenance
import initiative


LEASE={'tenantId':'tenant-A','botId':'bot-A','generation':7,'revision':3}
SELF='99999'
SCOPE='group:55555'


class MessageEvent:
    def __init__(self,event_id,parts=None,raw_parts=None):
        self.extras={}
        self.raw={'post_type':'message','message_type':'group','message_id':event_id,
            'self_id':SELF,'user_id':'12345','group_id':'55555',
            'time':int(time.time()),'sub_type':'normal','sender':{'user_id':'12345','nickname':'fixture'},
            'raw_message':'fixture','message':raw_parts if raw_parts is not None else []}
        self.message_obj=NS(message_id=event_id,timestamp=time.time(),raw_message=self.raw)
        self.parts=parts or []
    def get_group_id(self):return '55555'
    def get_sender_id(self):return '12345'
    def get_self_id(self):return SELF
    def get_platform_id(self):return 'qq'
    def get_messages(self):return self.parts
    def get_extra(self,key,default=None):return self.extras.get(key,default)
    def set_extra(self,key,value):self.extras[key]=value


class DeferredAddressRecovery(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE fishbot_shared_context(id INTEGER PRIMARY KEY,state TEXT,group_limit INTEGER)')
        self.db.execute("INSERT INTO fishbot_shared_context VALUES(1,'active',2)");self.db.commit()
        message_provenance.initialize(self.db)
        self.pipeline=BotPipeline(self.db)
    def tearDown(self):self.db.close()

    def defer_joined(self,parts,raw_parts=None,reason='auth_backoff',seen=None):
        root=MessageEvent('root')
        work=self.pipeline.bind(root,LEASE)
        joined=MessageEvent('joined',parts,raw_parts)
        self.assertTrue(self.pipeline.persist_input(joined,LEASE))
        self.assertTrue(self.pipeline.join_active(joined))
        if seen==1:self.pipeline.seen_inputs(work,[model_message(SCOPE,'joined','12345')])
        elif seen==3:self.pipeline.ignored_inputs(work,['joined'])
        work.stage('admission');work.stop(reason,'silent' if reason!='group_waiting' else 'waiting')
        self.pipeline.finish(work,{})
        self.pipeline.release_work(work)
        return joined

    def test_auth_backoff_recovers_raw_mention_without_new_message(self):
        joined=self.defer_joined([], [{'type':'at','data':{'qq':SELF}}])
        self.assertEqual(self.db.execute("SELECT status,addressed_self FROM pipeline_work_inputs WHERE event='joined'").fetchone(),('pending',1))
        self.pipeline.resume_deferred_mentions()
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='joined'").fetchone()[0],'pending')

    def test_sdk_resolved_reply_to_bot_recovers_but_raw_reply_claim_is_not_trusted(self):
        self.defer_joined([Reply(id='77',sender_id=SELF)], [{'type':'reply','data':{'id':'77'}}],reason='group_waiting')
        self.assertEqual(self.db.execute("SELECT status,addressed_self FROM pipeline_work_inputs WHERE event='joined'").fetchone(),('pending',1))

        other=MessageEvent('other',[],[{'type':'reply','data':{'id':'88','sender_id':SELF}}])
        root=MessageEvent('root-2');work=self.pipeline.bind(root,LEASE)
        self.assertTrue(self.pipeline.persist_input(other,LEASE));self.assertTrue(self.pipeline.join_active(other))
        work.stage('admission');work.stop('group_waiting','waiting');self.pipeline.finish(work,{});self.pipeline.release_work(work)
        self.assertEqual(self.db.execute("SELECT status,addressed_self FROM pipeline_work_inputs WHERE event='other'").fetchone(),('deferred',0))

    def test_recovery_dispatch_uses_retained_converter_and_restores_trusted_reply_address(self):
        self.defer_joined([Reply(id='77',sender_id=SELF)],[{'type':'reply','data':{'id':'77'}}])
        committed=[];extra={}
        message=NS(message_id='joined',timestamp=1,raw_message=None)
        class Platform:
            bot=NS()
            def meta(self):return NS(id='qq')
            async def convert_retained_message(self,raw):message.raw_message=raw;return message
            async def convert_message(self,raw):raise AssertionError('recovery must bypass live-ingress de-dup')
            def create_event(self,message):return NS(set_extra=lambda key,value:extra.update({key:value}))
            def commit_event(self,event):committed.append(event)
        platform=Platform()
        core=NS(event_queue=NS(qsize=lambda:0),platform_manager=NS(get_insts=lambda:[platform]),
            event_bus=NS(stopped_platforms=set(),_pending_tasks={}))
        store=NS(db=self.db,reflection=NS(checkpoint_issue=lambda *args:None))
        self.assertTrue(asyncio.run(initiative.resume_scope_input(core,'qq',LEASE,store,{'qq':LEASE})))
        self.assertEqual(len(committed),1)
        self.assertTrue(extra.get('_hosted_recovered_addressed_self'))

    def test_at_other_and_reply_other_and_ordinary_input_do_not_reenter(self):
        cases=[('other-at',[],[{'type':'at','data':{'qq':'88888'}}]),
               ('other-reply',[Reply(id='9',sender_id='88888')],[{'type':'reply','data':{'id':'9'}}]),
               ('ordinary',[],[{'type':'text','data':{'text':'普通消息'}}])]
        root=MessageEvent('root');work=self.pipeline.bind(root,LEASE)
        for event_id,parts,raw in cases:
            event=MessageEvent(event_id,parts,raw)
            self.assertTrue(self.pipeline.persist_input(event,LEASE));self.assertTrue(self.pipeline.join_active(event))
        work.stage('admission');work.stop('group_waiting','waiting');self.pipeline.finish(work,{});self.pipeline.release_work(work)
        for event_id,_,_ in cases:
            self.assertEqual(self.db.execute('SELECT status,addressed_self FROM pipeline_work_inputs WHERE event=?',(event_id,)).fetchone(),('deferred',0))
        self.pipeline.resume_deferred_mentions()
        self.assertTrue(all(row[0]=='deferred' for row in self.db.execute('SELECT status FROM pipeline_work_inputs WHERE event IN (\'other-at\',\'other-reply\',\'ordinary\')')))

    def test_seen_or_explicitly_ignored_input_never_resumes(self):
        for event_id,seen in [('seen',1),('ignored',3)]:
            root=MessageEvent('root-'+event_id);work=self.pipeline.bind(root,LEASE)
            joined=MessageEvent(event_id,[],[{'type':'at','data':{'qq':SELF}}])
            self.assertTrue(self.pipeline.persist_input(joined,LEASE));self.assertTrue(self.pipeline.join_active(joined))
            if seen==1:self.pipeline.seen_inputs(work,[model_message(SCOPE,event_id,'12345')])
            else:self.pipeline.ignored_inputs(work,[event_id])
            work.stage('admission');work.stop('auth_backoff','silent');self.pipeline.finish(work,{});self.pipeline.release_work(work)
            expected='settled' if seen==3 else 'deferred'
            self.assertEqual(self.db.execute('SELECT status,seen FROM pipeline_work_inputs WHERE event=?',(event_id,)).fetchone(),(expected,seen))

    def test_active_owner_child_ledger_or_prior_dispatch_blocks_recovery(self):
        self.defer_joined([], [{'type':'at','data':{'qq':SELF}}])
        self.db.execute("UPDATE pipeline_work_inputs SET status='deferred',owner='root' WHERE event='joined'")
        self.db.execute("INSERT INTO pipeline_scope_work VALUES(?,?,?,?,?)",(SCOPE,'another','12345',self.pipeline.runtime,1));self.db.commit()
        self.pipeline.resume_deferred_mentions()
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='joined'").fetchone()[0],'deferred')
        self.db.execute('DELETE FROM pipeline_scope_work WHERE scope=?',(SCOPE,))
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed) VALUES(?,?,?,?,'message','dispatch','unknown','unconfirmed_protocol_outcome',1,0)",(SCOPE,'12345','joined',time.time()))
        self.db.commit();self.pipeline.resume_deferred_mentions()
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='joined'").fetchone()[0],'deferred')

    def test_old_schema_null_address_migrates_only_from_authenticated_raw_at_on_restart(self):
        event=self.defer_joined([], [{'type':'at','data':{'qq':SELF}}])
        payload=self.db.execute("SELECT payload,payload_sha FROM pipeline_work_inputs WHERE event='joined'").fetchone()
        self.db.execute("UPDATE pipeline_work_inputs SET status='deferred',owner='root',addressed_self=NULL WHERE event='joined'");self.db.commit()
        self.pipeline=BotPipeline(self.db)
        self.assertEqual(self.db.execute("SELECT status,addressed_self FROM pipeline_work_inputs WHERE event='joined'").fetchone(),('pending',1))
        self.assertEqual(self.db.execute("SELECT payload,payload_sha FROM pipeline_work_inputs WHERE event='joined'").fetchone(),payload)

    def test_null_address_without_real_raw_at_is_migrated_to_ineligible(self):
        self.defer_joined([], [{'type':'at','data':{'qq':'88888'}}],reason='group_presence_busy_deferred')
        self.db.execute("UPDATE pipeline_work_inputs SET addressed_self=NULL WHERE event='joined'");self.db.commit()
        self.pipeline=BotPipeline(self.db)
        self.assertEqual(self.db.execute("SELECT status,addressed_self FROM pipeline_work_inputs WHERE event='joined'").fetchone(),('deferred',0))

    def test_pre_convergence_schema_adds_address_column_and_recovers_raw_at(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE fishbot_shared_context(id INTEGER PRIMARY KEY,state TEXT,group_limit INTEGER)')
        db.execute("INSERT INTO fishbot_shared_context VALUES(1,'active',2)")
        db.execute('''CREATE TABLE pipeline_work_inputs(scope TEXT,event TEXT,subject TEXT,owner TEXT,payload TEXT,seen INTEGER DEFAULT 0,status TEXT,created REAL,PRIMARY KEY(scope,event))''')
        message_provenance.initialize(db);db.commit()
        pipeline=BotPipeline(db)
        columns={row[1] for row in db.execute('PRAGMA table_info(pipeline_work_inputs)')}
        self.assertIn('addressed_self',columns)
        raw={'post_type':'message','message_type':'group','message_id':'legacy','self_id':SELF,'user_id':'12345','group_id':'55555','message':[{'type':'at','data':{'qq':SELF}}]}
        db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed) VALUES(?,?,?,?,'message','admission','waiting','auth_backoff',0,0)",(SCOPE,'12345','parent',time.time()))
        db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,owner,payload,seen,status,created,identity) VALUES(?,?,?,?,?,0,'deferred',?,?)",(SCOPE,'legacy','12345','parent',json.dumps(raw),time.time(),json.dumps(LEASE)))
        db.commit();pipeline.resume_deferred_mentions()
        self.assertEqual(db.execute("SELECT status,addressed_self FROM pipeline_work_inputs WHERE event='legacy'").fetchone(),('pending',1))
        db.close()


if __name__=='__main__':unittest.main(verbosity=2)
