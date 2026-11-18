"""Offline migration preparation cannot mint rounds or conceal stale evidence."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from behavior import BehaviorStore
from context_protocol import event_message,model_json
from memory_reflection import prepare_owner_migration_summary,verify_owner_migration_summary,publish_owner_migration_summary,summary_foundation
from summary_material import snapshots,remember


class OwnerSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.store=BehaviorStore(Path(self.temporary.name)/'behavior.sqlite')
        self.db=self.store.db
        channel='private:12345678';event=model_json([channel,'same-id'])
        message=event_message({'scope':channel,'sender_id':'12345678','event_id':'same-id','at':100},'周六一起画红色小船')
        material=snapshots([message,{'role':'assistant','content':'周六一起画。'}],'bot_shared')[0]
        material['reply']='周六一起画。'
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',('bot_shared','12345678',event,material['text'],material['reply'],100))
        self.db.execute('INSERT INTO dialogue_feedback(scope,subject,event,at,reply) VALUES(?,?,?,?,?)',(channel,'12345678','same-id',100,material['reply']))
        import message_provenance
        message_provenance.put(self.db,channel,'same-id','outgoing',{'receipt':'confirmed','complete':True,'message_ids':['reply-id']})
        remember(self.db,'bot_shared',[material]);self.db.commit()
        self.payload=[{'sender_id':'12345678','event':event,'user':material['text'],'bot':material['reply'],'at':100,
                       'source':{'scope':channel,'sender_id':'12345678','event_id':'same-id','at':100}}]
        self.requests=[]
        async def stream(**request):
            self.requests.append(request)
            yield SimpleNamespace(is_chunk=False,completion_text=json.dumps({'summary':'12345678提出周六共同绘画红色小船。',
                'evidence':[{'event':event,'who':'user','quote':'周六一起画红色小船'}],
                'conversation_continuity':[{'source_scope':channel,'text':'双方接着商量周六画船。'}]},ensure_ascii=False),
                raw_completion={'choices':[{'finish_reason':'stop'}]})
        self.provider=SimpleNamespace(text_chat_stream=stream,provider_config={'id':'fixture','model':'fixture'})

    async def asyncTearDown(self):
        self.store.close();self.temporary.cleanup()

    async def test_prepare_validates_without_database_writes_or_fabricated_rounds(self):
        before='\n'.join(self.db.iterdump())
        product=await prepare_owner_migration_summary(self.db,self.payload,self.provider,{'ownerUin':'12345678'})
        self.assertTrue(verify_owner_migration_summary(self.db,self.payload,product))
        self.assertEqual('\n'.join(self.db.iterdump()),before)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(len(product['claims']),1)
        self.assertEqual(product['claims'][0]['speaker']['id'],'12345678')
        self.assertTrue(product['claims'][0]['sourceRefs'][0]['event'].startswith('semantic:'))
        self.assertIsNotNone(product['continuity'])

    async def test_transactional_verification_rejects_changed_material_and_product(self):
        product=await prepare_owner_migration_summary(self.db,self.payload,self.provider,{})
        changed=copy.deepcopy(product);changed['summary']='伪造摘要'
        with self.assertRaisesRegex(ValueError,'product_changed'):
            verify_owner_migration_summary(self.db,self.payload,changed)
        self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated) VALUES('bot_shared','新增认知','[]',1)")
        with self.assertRaisesRegex(ValueError,'basis_changed'):
            verify_owner_migration_summary(self.db,self.payload,product)

    async def test_stale_model_result_does_not_write(self):
        with self.assertRaisesRegex(RuntimeError,'summary_stale'):
            await prepare_owner_migration_summary(self.db,self.payload,self.provider,{},live=lambda:False)
        self.assertEqual(self.requests,[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)

    async def test_unbound_native_and_tools_never_reach_model_or_claims(self):
        event=self.payload[0]['event']
        material=json.loads(self.db.execute('SELECT material FROM memory_round_material WHERE event=?',(event,)).fetchone()[0])
        material.update(version=2,native_reply='UNBOUND_NATIVE_TEXT',tools=[{'id':'call','name':'UNBOUND_TOOL',
            'arguments':'UNBOUND_ARGUMENT','result':'UNBOUND_RESULT'}])
        self.db.execute('UPDATE memory_round_material SET material=? WHERE event=?',(model_json(material),event));self.db.commit()
        product=await prepare_owner_migration_summary(self.db,self.payload,self.provider,{})
        request=model_json(self.requests[0])
        self.assertNotIn('UNBOUND_',request)
        self.assertEqual(product['omittedUnknowns'],{'nativeOutputs':1,'toolRounds':1,'unverifiedReplies':0})
        self.assertNotIn('UNBOUND_',model_json(product['claims']))
        saved=self.db.execute('SELECT material FROM memory_round_material WHERE event=?',(event,)).fetchone()[0]
        self.assertIn('UNBOUND_NATIVE_TEXT',saved)

    async def test_text_witness_without_confirmed_receipt_does_not_prove_reply_sent(self):
        import message_provenance
        message_provenance.put(self.db,'private:12345678','same-id','outgoing',
            {'receipt':'unknown','complete':False,'message_ids':[]})
        self.db.commit()
        product=await prepare_owner_migration_summary(self.db,self.payload,self.provider,{})
        prompt=json.loads(self.requests[0]['prompt'])
        self.assertEqual(prompt['turns'][0]['bot'],'')
        self.assertEqual(product['omittedUnknowns']['unverifiedReplies'],1)

    async def test_import_order_has_no_cycle(self):
        import subprocess
        for modules in ('memory_reflection, journal_cutover, readonly_journal',
                        'readonly_journal, journal_cutover, memory_reflection'):
            result=subprocess.run([sys.executable,'-B','-c',
                'import sys;sys.path[:0]='+repr(sys.path[:2])+';import '+modules],
                capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)

    async def test_incomplete_physical_blocks_are_not_replayed_to_model(self):
        from semantic_collection import Collected
        payload=Collected(self.payload,[{'event':model_json(['group:11111','missing']),
            'source_scope':'group:11111','sender_ids':['11111'],'missing':'incomplete_original',
            'available_text':['可靠的用户输入'],'available_reply':['UNBOUND_REPLY'],
            'available_observations':[[{'role':'assistant','content':'UNBOUND_NATIVE',
                'tool_calls':[{'name':'UNBOUND_TOOL'}]},{'role':'tool','content':'UNBOUND_RESULT'}]],'images':[]}])
        product=await prepare_owner_migration_summary(self.db,payload,self.provider,{})
        self.assertNotIn('UNBOUND_',model_json(self.requests[0]))
        self.assertEqual(product['omittedUnknowns'],{'nativeOutputs':3,'toolRounds':1,'unverifiedReplies':1})

    async def test_publish_merges_old_products_into_one_visible_foundation_and_rolls_back(self):
        old_ids=[]
        for index in range(4):
            claim={'id':'oldclaim'+str(index),'text':'旧认知'+str(index),'kind':'human_statement',
                   'speaker':{'role':'human','id':'12345678'},'sourceRefs':[{'scope':'private:12345678','event':'semantic:old'+str(index),'role':'user'}],
                   'visibility':'private:12345678','occurredAt':50+index,'status':'active'}
            identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',
                ('private:12345678','旧认知'+str(index),'[]',50+index,json.dumps([claim]))).lastrowid
            self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
            self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,'private:12345678'))
            self.db.execute('INSERT INTO memory_summary_people VALUES(?,?)',(identifier,'12345678'))
            old_ids.append(identifier)
        self.db.commit()
        product=await prepare_owner_migration_summary(self.db,self.payload,self.provider,{})
        before='\n'.join(self.db.iterdump())
        self.db.execute('BEGIN IMMEDIATE')
        result=publish_owner_migration_summary(self.db,self.payload,product)
        self.assertEqual(result['consumedSummaryIds'],old_ids)
        self.assertEqual(result['claimsCount'],5)
        basis=summary_foundation(self.db,'bot_shared')
        self.assertEqual([row['id'] for row in basis],[result['summaryId']])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        raw=json.loads(self.db.execute('SELECT claims FROM memory_summaries').fetchone()[0])
        inherited=next(claim for claim in raw if claim['id']=='oldclaim0')
        self.assertEqual(inherited['parents'],[{'summary':old_ids[0],'claim':'oldclaim0'}])
        self.db.rollback()
        self.assertEqual('\n'.join(self.db.iterdump()),before)
        self.assertTrue(verify_owner_migration_summary(self.db,self.payload,product))

    async def test_publish_rejects_loss_of_protected_source_claims(self):
        claims=[{'id':'protected'+str(index),'text':'仍需处理的认知'+str(index),'kind':'human_statement',
            'speaker':{'role':'human','id':'12345678'},'protected':True,
            'sourceRefs':[{'scope':'private:12345678','event':'semantic:old'+str(index),'role':'user'}],
            'visibility':'private:12345678','occurredAt':index+1,'status':'active'} for index in range(31)]
        identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES(?,?,?,?,2,?)',
            ('private:12345678','受保护认知','[]',50,model_json(claims))).lastrowid
        self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'small')",(identifier,))
        self.db.commit()
        product=await prepare_owner_migration_summary(self.db,self.payload,self.provider,{})
        before='\n'.join(self.db.iterdump())
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaisesRegex(ValueError,'protected_claims_uncarried:'+str(identifier)):
            publish_owner_migration_summary(self.db,self.payload,product)
        self.db.rollback()
        self.assertEqual('\n'.join(self.db.iterdump()),before)

    async def test_two_scope_cutover_failure_rollback_retry_and_restart(self):
        import hashlib,sqlite3
        from unittest.mock import patch
        from uuid import uuid4
        import journal_storage,journal_cutover
        from readonly_journal import ReadonlyJournal
        for table in ('memory_pending','memory_round_material','dialogue_feedback','memory_message_sources'):
            self.db.execute('DELETE FROM '+table)
        ReadonlyJournal(self.db,None)
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='fishbot_shared_context'").fetchone():
            self.db.execute('DELETE FROM fishbot_shared_context')
        self.db.execute('DELETE FROM fishbot_readonly_journals')
        expected={}
        for scope,at in (('private:11111',20),('group:22222',10)):
            message=event_message({'scope':scope,'sender_id':'11111','event_id':'same-id','at':at},'真实输入'+scope)
            messages=[{'role':'system','content':'stable system'},message,{'role':'assistant','content':'未绑定原稿'}]
            captured=snapshots(messages,scope)
            self.db.execute('INSERT INTO memory_rounds(scope,subject,event,question,answer,at) VALUES(?,?,?,?,?,?)',
                (scope,'11111','same-id',captured[0]['text'],'',at))
            remember(self.db,scope,captured)
            value={'scope':model_json(scope),'base':messages,'wire':messages,'state':{},'turns':1,'at':30,
                   'group_turn':None,'base_chars':0,'detail':'','usage':{},'authority':None,'retirement':None}
            raw,blobs=journal_storage.encode(value);self.assertFalse(blobs)
            sha=hashlib.sha256(raw.encode()).hexdigest();expected[scope]=sha
            self.db.execute("INSERT INTO fishbot_readonly_journals VALUES(?,?,'ready',?,?,30)",(model_json(scope),2,raw,sha))
        self.db.commit()
        self.db.execute('BEGIN')
        payload=journal_cutover.collect_owner_history(self.db,expected)
        self.assertEqual([row['at'] for row in payload],[10,20])
        self.assertEqual(len({row['event'] for row in payload}),2)
        self.db.commit()
        product=await prepare_owner_migration_summary(self.db,payload,self.provider,{})
        bot=str(uuid4());grant={'type':journal_cutover.OWNER_EARLY_MIGRATION,'id':str(uuid4()),'bot':bot,
            'destination':'bot_shared','sources':expected,'reason':'owner authorized test','release':'a'*40}
        from shared_context import initialize
        initialize(self.db)
        before='\n'.join(self.db.iterdump())
        original=ReadonlyJournal.retire_owner_source
        calls=[]
        def failing(db,scope,sha,authorization):
            calls.append(scope)
            if len(calls)==2:raise RuntimeError('second_source_failure')
            return original(db,scope,sha,authorization)
        with patch.object(ReadonlyJournal,'retire_owner_source',side_effect=failing):
            with self.assertRaisesRegex(RuntimeError,'second_source_failure'):
                journal_cutover.retire_owner_journals(self.db,bot,grant,'b'*64,{'groupParticipationGroups':['22222']},product)
        self.assertEqual('\n'.join(self.db.iterdump()),before)
        result=journal_cutover.retire_owner_journals(self.db,bot,grant,'b'*64,{'groupParticipationGroups':['22222']},product)
        self.assertEqual(result['historyCount'],2)
        self.assertEqual(self.db.execute("SELECT scope FROM fishbot_readonly_journals WHERE state='ready'").fetchall(),[(model_json('bot_shared'),)])
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
        self.assertEqual(result,journal_cutover.retire_owner_journals(self.db,bot,grant,'b'*64,{},product))
        restored=sqlite3.connect(':memory:');self.addCleanup(restored.close);self.db.backup(restored)
        self.assertEqual(result,journal_cutover.retire_owner_journals(restored,bot,grant,'b'*64,{},product))


if __name__=='__main__':unittest.main()
