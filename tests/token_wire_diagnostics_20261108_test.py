"""Offline diagnostics: final HTTP metadata, bounded attribution, no network."""
import contextvars,json,sys,tempfile,time,unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import httpx
import model_cache as m
import model_request_capture as capture


class WireDiagnosticsTests(unittest.TestCase):
    def test_wire_metadata_history_is_bounded_and_never_shared_between_tenants(self):
        lease={'tenantId':'one','botId':'same-bot','root':'unused'}
        event=NS(get_extra=lambda key,default=None:lease if key=='_hosted_lease' else NS(scope='shared',event_id='w') if key=='_hosted_pipeline' else default)
        token=m.begin_attempt(event,NS());rows=[]
        try:
            with patch.object(m,'_wire_shapes',OrderedDict()),patch.object(m,'_wire_signatures',OrderedDict()),patch.object(m,'record_diagnostic',side_effect=lambda _,row:rows.append(row)):
                payload={'messages':[],'model':'model'}
                for index in range(36):
                    lease['tenantId']='tenant-'+str(index)
                    m.observe_wire_request(NS(content=json.dumps(payload).encode()))
                    self.assertFalse(json.loads(rows[-1]['diagnosticDetail'])['wireBaselineKnown'])
                self.assertEqual(len(m._wire_shapes),32);self.assertEqual(len(m._wire_signatures),32)
                m.observe_wire_request(NS(content=json.dumps(payload).encode()))
                self.assertTrue(json.loads(rows[-1]['diagnosticDetail'])['wireBaselineKnown'])
        finally:m.reset_attempt(token)

    def test_changes_are_wire_only_model_scoped_and_content_free(self):
        lease={'tenantId':'wire-test-tenant','botId':'wire-test-bot','root':'unused'}
        event=NS(get_extra=lambda key,default=None:lease if key=='_hosted_lease' else NS(scope='shared',event_id='w') if key=='_hosted_pipeline' else default)
        token=m.begin_attempt(event,NS());rows=[]
        try:
            with patch.object(m,'_wire_shapes',{}),patch.object(m,'_wire_signatures',{}),patch.object(m,'record_diagnostic',side_effect=lambda _,row:rows.append(row)):
                payload={'model':'one','messages':[{'role':'user','content':'private chat'}],'tools':[{'private':'tool'}],'prompt_cache_key':'private routing','temperature':1}
                def observe():
                    raw=json.dumps(payload).encode()
                    request=NS(content=raw);m.observe_wire_request(request)
                    self.assertEqual(request.content,raw)
                    return json.loads(rows[-1]['diagnosticDetail'])
                first=observe();self.assertFalse(first['wireBaselineKnown']);self.assertIsNone(first['toolsChanged'])
                same=observe();self.assertTrue(same['wireBaselineKnown']);self.assertFalse(same['toolsChanged']);self.assertFalse(same['parametersChanged']);self.assertFalse(same['routingChanged'])
                payload['tools']=[];self.assertTrue(observe()['toolsChanged'])
                payload['temperature']=.5;self.assertTrue(observe()['parametersChanged'])
                payload['prompt_cache_key']='second private routing';self.assertTrue(observe()['routingChanged'])
                payload['model']='two';self.assertFalse(observe()['wireBaselineKnown'])
                self.assertNotIn('private',json.dumps(rows))
                self.assertEqual(rows[1]['prefixMessages'],1)
        finally:m.reset_attempt(token)

    def test_capture_phase_filter_does_not_consume_slots_or_cross_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);lease={'root':directory};cv=contextvars.ContextVar('capture-event',default=None)
            event=NS(get_extra=lambda key:lease,get_platform_id=lambda:'bot',message_obj=NS(message_id='e'))
            arm={'botId':'bot','model':'test-model','session':'d'*24,'expiresAt':time.time()+600,'maxRequests':1,'phase':'style_learning'}
            (root/'model-request-capture.json').write_text(json.dumps(arm))
            request=httpx.Request('POST','https://unit.invalid/v1/chat/completions',json={'model':'test-model','messages':[{'role':'user','content':'private sample'}]},headers={'Authorization':'never persist'})
            token=cv.set(event)
            try:
                with patch.dict(sys.modules,{'astrbot.core.platform.astr_message_event':NS(current_message_event=cv)}):
                    self.assertIsNone(capture.begin(request))
                    attempt=m._attempt.set({'lease':dict(lease),'phase':'style_learning','attemptId':'wrong-lease','wireIndex':1})
                    try:self.assertIsNone(capture.begin(request))
                    finally:m.reset_attempt(attempt)
                    attempt=m._attempt.set({'lease':lease,'phase':'foreground','attemptId':'foreground-id','wireIndex':1})
                    try:self.assertIsNone(capture.begin(request))
                    finally:m.reset_attempt(attempt)
                    self.assertFalse((root/'model-request-captures').exists())
                    attempt=m._attempt.set({'lease':lease,'phase':'style_learning','attemptId':'style-id','wireIndex':2})
                    try:
                        result=capture.begin(request);self.assertIsNotNone(result);self.assertIsNone(capture.begin(request))
                        result.feed(b'{"choices":[{"message":{"content":"private output"},"finish_reason":"length"}],"usage":{"prompt_tokens":4863}}')
                        result.finish(True)
                    finally:m.reset_attempt(attempt)
                folder=root/'model-request-captures'/('d'*24)
                req=json.loads((folder/'0.request.json').read_text());response=json.loads((folder/'0.response.json').read_text())
                self.assertEqual((req['phase'],req['attemptId'],req['wireIndex']),('style_learning','style-id',2))
                self.assertNotIn('never persist',json.dumps(req));self.assertEqual(response['finishReasons'],['length'])
                self.assertNotIn('private output',json.dumps(response));self.assertEqual(response['usage'][0]['prompt_tokens'],4863)
            finally:cv.reset(token)


if __name__=='__main__':unittest.main()
