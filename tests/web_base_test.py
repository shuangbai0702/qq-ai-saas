import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from context_protocol import persona_reference, persona_prompt
from readonly_journal import ReadonlyJournal, ContextPrefixViolation
from system_contract import SystemContract
from web_base import persona_view, sha, without_sdk_persona


class WebBaseTests(unittest.TestCase):
    def proof(self):return {'target':sha('new persona'),'origins':[sha('old persona')]}

    def test_sdk_duplicate_is_removed_without_touching_other_instructions(self):
        sdk='SDK rules\n# Persona Instructions\n\nold persona\ntool instruction'
        self.assertEqual(without_sdk_persona(sdk,'old persona'),'SDK rulestool instruction')
        self.assertEqual(without_sdk_persona(sdk,'unrelated'),sdk)
        old=[{'role':'system','content':'platform'},persona_reference('old persona'),persona_reference(sdk),{'role':'user','content':'history'}]
        changed=persona_view(old,old,self.proof(),'new persona')
        self.assertEqual(changed,[old[0],persona_reference('new persona'),persona_reference('SDK rulestool instruction'),old[3]])
        self.assertIn('old persona',old[2]['content'])
        self.assertEqual(persona_view(changed,changed,self.proof(),'new persona'),changed)

    def test_old_sdk_selection_waiting_for_writer_uses_new_request_snapshot(self):
        from context_transport import request_context_view
        sdk='SDK rules\n# Persona Instructions\n\nold persona\ntool instruction'
        _,messages=request_context_view('new persona',[{'role':'system','content':sdk}],None,'','','{}',platform_system='fixed',persona_proof=self.proof())
        rendered=json.dumps(messages)
        self.assertNotIn('old persona',rendered)
        self.assertEqual(rendered.count('new persona'),1)
        self.assertIn('SDK rulestool instruction',rendered)

    def test_legacy_sdk_combined_system_updates_only_exact_proven_persona(self):
        from context_transport import AUTHORITY_GUIDANCE
        prefix='SDK rules\n# Persona Instructions\n\nold persona\ntool instruction'
        suffix='\n'+AUTHORITY_GUIDANCE+'\nunchanged platform instructions'
        old=[{'role':'system','content':prefix+suffix},{'role':'user','content':'real history'}]
        actual=persona_view(old,old,self.proof(),'new persona')
        self.assertEqual(actual[0]['content'],prefix.replace('old persona','new persona')+suffix)
        self.assertEqual(actual[1:],old[1:])

    def test_only_persona_changes_even_if_fresh_sdk_system_changed(self):
        old=[{'role':'system','content':'fixed platform'},persona_reference('old persona'),
             {'role':'user','content':[{'type':'text','text':'real input'},{'type':'image_url','image_url':{'url':'data:image/png;base64,AA=='}}]},
             {'role':'assistant','content':None,'tool_calls':[{'id':'call-1','type':'function','function':{'name':'test','arguments':'{}'}}]},
             {'role':'tool','tool_call_id':'call-1','content':'real result'},
             {'role':'system','content':'historical System is NOT configuration'}]
        original=copy.deepcopy(old)
        incoming=[{'role':'system','content':'unrequested code update'},persona_reference('new persona'),*old[2:]]
        changed=persona_view(old,incoming,self.proof(),'new persona')
        self.assertEqual(changed,[old[0],persona_reference('new persona'),*old[2:]])
        self.assertEqual(old,original)
        self.assertEqual(persona_view(old,incoming,None,'new persona'),incoming)
        with self.assertRaises(ContextPrefixViolation):persona_view(old,incoming,self.proof(),'different revision')

    def test_legacy_combined_system_keeps_guidance_and_roles(self):
        from context_transport import AUTHORITY_GUIDANCE
        for rendered in ('old persona',persona_prompt('old persona',images=True,chat=True)):
            with self.subTest(rendered=rendered):
                suffix='\n'+AUTHORITY_GUIDANCE+'\nfixed old guidance'
                old=[{'role':'system','content':rendered+suffix},{'role':'user','content':'actual history'}]
                actual=persona_view(old,old,self.proof(),'new persona')
                self.assertEqual(actual[0]['role'],'system')
                self.assertTrue(actual[0]['content'].endswith(suffix))
                self.assertNotIn('old persona',actual[0]['content'])
                self.assertEqual(actual[1:],old[1:])
        with self.assertRaises(ContextPrefixViolation):
            persona_view([{'role':'system','content':'unverifiable old preamble'}],[],self.proof(),'new persona')

    def test_immediate_journal_update_and_final_http_wire_without_checkpoint(self):
        import httpx
        from model_cache import begin_attempt,expect_append_only_messages,observe_wire_request,finish_attempt,reset_attempt
        with tempfile.TemporaryDirectory() as root:
            db=sqlite3.connect(':memory:');self.addCleanup(db.close)
            guard=SystemContract(db);journal=ReadonlyJournal(db,'bot_shared')
            old=[{'role':'system','content':'fixed'},persona_reference('old persona'),{'role':'user','content':'first input'}]
            from context_protocol import READONLY_CURRENT
            packet={'role':'user','content':READONLY_CURRENT+'\n{}'}
            first=journal.prepare(old+[packet],1);self.assertTrue(journal.commit(first))
            guard.reserve(first.wire,None,'initial',1,scope='bot_shared')
            changed=persona_view(first.base,[*old,{'role':'user','content':'next input'}],self.proof(),'new persona')
            candidate=journal.prepare(changed+[packet],2,reconstruction_scope='bot_shared',system_transition_authorized=True,web_base_authorized=True)
            self.assertIsNone(candidate.reconstruction_permit)
            self.assertEqual(candidate.wire[2:len(first.wire)],first.wire[2:])
            self.assertTrue(journal.stage_candidate(candidate))
            lease={'root':root,'tenantId':'test','botId':'test','revision':2}
            event=NS(get_extra=lambda key,default=None:lease if key=='_hosted_lease' else 'bot_shared' if key=='_hosted_journal_scope' else NS(source='message',scope='private:12345',event_id='test') if key=='_hosted_pipeline' else default)
            token=begin_attempt(event,object(),system_contract=guard,system_permit_id='42')
            try:
                expect_append_only_messages(candidate.wire)
                wire=httpx.Request('POST','https://fixture.invalid/v1/chat/completions',json={'messages':candidate.wire})
                observe_wire_request(wire);finish_attempt('completed')
            finally:reset_attempt(token)
            self.assertTrue(journal.commit(candidate))
            restored=ReadonlyJournal(db,'bot_shared')
            self.assertEqual(restored.last.wire,candidate.wire)
            self.assertEqual(restored.last.wire[1],persona_reference('new persona'))
            self.assertEqual(db.execute("SELECT count(*) FROM fishbot_system_permits WHERE state='consumed'").fetchone()[0],1)
            db.close()


if __name__=='__main__':unittest.main()
