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
    def test_only_authenticated_base_replacement_keeps_execution_ownership(self):
        from web_base import same_owner
        from tool_config import apply_user_tools,tool_hash
        token=object()
        old={'botId':'b','tenantId':'t','generation':1,'revision':1,'config':{'toolPrompts':{'dsh':'old'}},'_base_owner':token}
        config={'toolPrompts':{'dsh':'new'}}
        changed=apply_user_tools(old,{'botId':'b','tenantId':'t','generation':1,'revision':2,'config':config,'toolsEdit':{'permitId':'1','target':tool_hash(config)}})
        self.assertTrue(same_owner(old,changed))
        self.assertFalse(same_owner(old,{**changed,'_base_owner':object()}))
        self.assertFalse(same_owner(old,{k:v for k,v in changed.items() if k!='_base_owner'}))

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
        for xml in (False,True):
            with self.subTest(xml=xml):self.check_immediate_http_wire(xml)

    def check_immediate_http_wire(self,xml):
        import httpx
        from model_cache import begin_attempt,expect_append_only_messages,observe_wire_request,finish_attempt,reset_attempt
        with tempfile.TemporaryDirectory() as root:
            db=sqlite3.connect(':memory:');self.addCleanup(db.close)
            guard=SystemContract(db);journal=ReadonlyJournal(db,'bot_shared')
            persona={'role':'system','content':'<safety_instructions>fixed</safety_instructions>\n<persona_instructions>\nold persona\n</persona_instructions>\n<tool_use_instructions>fixed tools</tool_use_instructions>'} if xml else persona_reference('old persona')
            old=[{'role':'system','content':'fixed'},persona,{'role':'user','content':'first input'}]
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
            self.assertEqual(restored.last.wire[1],{'role':'system','content':persona['content'].replace('old persona','new persona')} if xml else persona_reference('new persona'))
            self.assertEqual(db.execute("SELECT count(*) FROM fishbot_system_permits WHERE state='consumed'").fetchone()[0],1)
            db.close()


class LegacyXmlPersonaTests(unittest.TestCase):
    def test_old_xml_sdk_replaces_only_proven_persona_and_keeps_history(self):
        from web_base import legacy_sdk_persona
        prefix='<safety_instructions>fixed rules</safety_instructions>\n\n'
        suffix='\n</persona_instructions>\n<tool_use_instructions>fixed tools</tool_use_instructions>'
        sdk=prefix+'<persona_instructions>\nold persona'+suffix
        prior=[{'role':'system','content':'unchanged platform'},{'role':'system','content':sdk},{'role':'user','content':'actual history'},{'role':'system','content':sdk}]
        proof={'target':sha('new persona'),'origins':[sha('old persona')]}
        changed=persona_view(prior,prior,proof,'new persona')
        self.assertEqual(changed[0],prior[0]);self.assertEqual(changed[2:],prior[2:])
        self.assertEqual(changed[1]['content'],sdk.replace('old persona','new persona'))
        self.assertEqual(persona_view(changed,changed,proof,'new persona'),changed)
        self.assertIsNone(legacy_sdk_persona(sdk,[], 'unrelated',replace=True))
        self.assertIsNone(legacy_sdk_persona(sdk+sdk,proof['origins'],'new persona',replace=True))
        stripped=without_sdk_persona(sdk,'old persona')
        self.assertNotIn('old persona',stripped);self.assertIn('fixed rules',stripped);self.assertIn('fixed tools',stripped)

    def test_xml_sdk_duplicate_becomes_one_persona_reference(self):
        sdk='SDK\n<persona_instructions>\nold persona\n</persona_instructions>\nTools'
        old=[{'role':'system','content':'platform'},persona_reference('old persona'),persona_reference(sdk),{'role':'user','content':'history'}]
        changed=persona_view(old,old,{'target':sha('new persona'),'origins':[sha('old persona')]},'new persona')
        self.assertNotIn('old persona',json.dumps(changed));self.assertEqual(json.dumps(changed).count('new persona'),1)
        self.assertEqual(changed[-1],old[-1])

if __name__=='__main__':unittest.main()
