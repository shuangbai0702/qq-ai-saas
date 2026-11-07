"""Offline boundary tests; mocked HTTP transports never create model traffic."""
import asyncio,contextvars,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import httpx
import model_cache as m
from model_egress import GuardedTransport

class Bytes(httpx.AsyncByteStream):
    async def __aiter__(self):yield b'{"choices":[]}'
    async def aclose(self):pass

class ObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.provider=NS()
        self.lease={'tenantId':'tenant','botId':'bot','root':self.tmp.name}
        m.bind_system_contract(self.provider,object(),self.lease)
    def tearDown(self):m.unbind_system_contract(self.provider);self.tmp.cleanup()
    def rows(self):return [json.loads(s) for s in (Path(self.tmp.name)/'model-diagnostics.jsonl').read_text().splitlines()]
    async def invoke(self,fail=False):
        token=m.begin_attempt(None,self.provider)
        transport=GuardedTransport();payload={'model':'test-model','messages':[{'role':'user','content':'sensitive body'}]}
        try:
            m.observe_payload(payload)
            request=httpx.Request('POST','https://unit.invalid/chat/completions',json=payload)
            if fail:
                async def delayed(*args):await asyncio.Event().wait()
                with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',delayed):await transport.handle_async_request(request)
            else:
                async def answer(*args):return httpx.Response(200,stream=Bytes())
                with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',answer):
                    response=await transport.handle_async_request(request);await response.aread();await response.aclose()
            m.finish_attempt('completed',NS(raw_completion={'id':'safe-id','usage':{'prompt_tokens':12,'completion_tokens':3,'prompt_cache_hit_tokens':7}}))
        except asyncio.CancelledError:m.finish_attempt('canceled',reason='CancelledError');raise
        finally:await transport.aclose();m.reset_attempt(token)
    async def test_embedding_entry_uses_same_diagnostics_and_restores_parent_attempt(self):
        import memory_reflection
        async def embedding(*_):
            transport=GuardedTransport()
            request=httpx.Request('POST','https://fixture.invalid/v1/embeddings',json={'model':'embedding-fixture','input':['private embedding input']})
            async def answer(*_):return httpx.Response(200,stream=Bytes())
            try:
                with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',answer):
                    response=await transport.handle_async_request(request);await response.aread();await response.aclose()
                return NS(usage={'prompt_tokens':11},data=[])
            finally:await transport.aclose()
        with patch.object(memory_reflection,'_embedding_request',embedding):
            await memory_reflection.embedding_request(self.provider,'','embedding-fixture',['private embedding input'])
        rows=self.rows();result=next(r for r in rows if r['kind']=='request_result')
        self.assertEqual(result['phase'],'embedding');self.assertEqual(result['inputTokens'],11)
        self.assertEqual(result['model'],'embedding-fixture');self.assertEqual(result['wireCount'],1)
        self.assertIsNone(m._attempt.get());self.assertEqual(rows[-1]['reason'],'embedding_response_returned')

    async def test_provider_fallback_returns_lease_and_final_wire_is_traceable(self):
        self.assertIs(m.current_lease(None,self.provider),self.lease)
        self.assertIsNone(m.current_lease(None,NS()))
        with m.auxiliary_scope(self.provider,'group_presence','event','group:12345'):
            await self.invoke();m.adopted('silent_decision_applied')
        rows=self.rows();self.assertEqual(len({r['attemptId'] for r in rows}),1)
        self.assertEqual([r for r in rows if r['kind']=='wire_result'][0]['httpStatus'],200)
        result=next(r for r in rows if r['kind']=='request_result')
        self.assertEqual((result['inputTokens'],result['cachedTokens'],result['model']),(12,7,'test-model'))
        self.assertEqual(rows[-1]['adoption'],'adopted')
        self.assertNotIn('sensitive body',json.dumps(rows));self.assertNotIn('unit.invalid',json.dumps(rows))
        self.assertIsNone(m._auxiliary.get());self.assertIsNone(m._attempt.get())
    async def test_timeout_correlates_canceled_http_with_unadopted_result(self):
        with self.assertRaises(TimeoutError):
            with m.auxiliary_scope(self.provider,'owner_contact'):
                await asyncio.wait_for(self.invoke(True),.01)
        rows=self.rows();result=next(r for r in rows if r['kind']=='request_result')
        self.assertEqual(result['diagnosticDetail'],'request_submission_unknown')
        self.assertEqual(rows[-1]['reason'],'TimeoutError');self.assertEqual(rows[-1]['adoption'],'not_adopted')
        self.assertTrue(any(r['kind']=='wire_result' and r['status']=='canceled' for r in rows))
    async def test_success_not_adopted_and_retry_wires_remain_distinct(self):
        with m.auxiliary_scope(self.provider,'small_summary'):
            await self.invoke();m.discarded('epoch_changed')
        self.assertEqual(self.rows()[-1]['reason'],'epoch_changed')
        token=m.begin_attempt(None,self.provider)
        try:
            for status in (503,200):
                m.observe_wire_request(NS(content=b'{"model":"m","messages":[]}'))
                wire=m.transport_started();m.transport_result(wire,'completed',http_status=status)
            m.finish_attempt('completed')
        finally:m.reset_attempt(token)
        results=[r for r in self.rows() if r['kind']=='wire_result'][-2:]
        self.assertEqual([r['wireIndex'] for r in results],[1,2])
        self.assertEqual(self.rows()[-1]['wireCount'],2)
    async def test_final_wire_rejection_is_not_mislabeled_as_sent(self):
        token=m.begin_attempt(None,self.provider)
        try:
            m.expect_append_only_messages([{'role':'user','content':'immutable'}])
            with self.assertRaises(m.AppendOnlyWireViolation):m.observe_wire_request(NS(content=b'{"messages":[]}'))
            m.finish_attempt('failed',reason='AppendOnlyWireViolation')
            self.assertEqual(self.rows()[-1]['diagnosticDetail'],'request_not_sent')
        finally:m.reset_attempt(token)
    async def test_oversize_diagnostic_preserves_identity_and_outcome(self):
        self.assertTrue(m.record_diagnostic(self.lease,{'kind':'request_result','status':'failed','reason':'X'*300,'diagnosticDetail':'Y'*2000}))
        row=self.rows()[-1];self.assertEqual(row['status'],'failed');self.assertEqual(row['reason'],'diagnostic_field_oversize')
    async def test_stream_closed_before_eof_is_unknown_once(self):
        token=m.begin_attempt(None,self.provider)
        try:
            wire=m.transport_started()
            from model_egress import LimitedStream
            stream=LimitedStream(Bytes(),wire,200);await stream.aclose();await stream.aclose()
            results=[r for r in self.rows() if r['kind']=='wire_result']
            self.assertEqual(len(results),1);self.assertEqual(results[0]['status'],'unknown')
        finally:m.reset_attempt(token)
    async def test_concurrent_provider_owners_do_not_share_scope_or_destination(self):
        with tempfile.TemporaryDirectory() as other:
            p=NS();m.bind_system_contract(p,object(),dict(self.lease,root=other,botId='other'))
            ready=asyncio.Event()
            async def first():
                with m.auxiliary_scope(self.provider,'style_learning'):
                    await ready.wait();await self.invoke();m.adopted()
            async def second():
                with m.auxiliary_scope(p,'summary_image'):
                    token=m.begin_attempt(None,p)
                    try:ready.set();await asyncio.sleep(0);m.finish_attempt('completed');m.adopted()
                    finally:m.reset_attempt(token)
            try:await asyncio.gather(first(),second())
            finally:m.unbind_system_contract(p)
            self.assertTrue(all(r['botId']=='bot' and r['phase']=='style_learning' for r in self.rows()))
            rows=[json.loads(s) for s in (Path(other)/'model-diagnostics.jsonl').read_text().splitlines()]
            self.assertTrue(all(r['botId']=='other' and r['phase']=='summary_image' for r in rows))

if __name__=='__main__':unittest.main()

