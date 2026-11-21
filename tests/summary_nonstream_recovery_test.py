"""Bounded same-provider recovery for read-only checkpoint requests."""
import asyncio,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from summary_response import summary_model_output

def valid():
    return NS(completion_text='{"summary":"complete"}',raw_completion={'choices':[{'finish_reason':'stop'}]})

class Recovery(unittest.IsolatedAsyncioTestCase):
    async def test_nonretryable_http_errors_do_not_repeat_paid_requests(self):
        import httpx
        from openai import AuthenticationError,PermissionDeniedError,RateLimitError
        request=httpx.Request('POST','https://summary.example/v1/chat/completions')
        for cls,status in [(AuthenticationError,401),(PermissionDeniedError,403),(RateLimitError,429)]:
            calls=[]
            error=cls('request rejected',response=httpx.Response(status,request=request),body=None)
            async def stream(**kw):
                calls.append('stream');raise error
                yield
            async def chat(**kw):calls.append('chat');return valid()
            with self.subTest(status=status),self.assertRaises(cls):
                await summary_model_output(NS(text_chat_stream=stream,text_chat=chat),{},lambda:True,{},800)
            self.assertEqual(calls,['stream'])

    async def test_invalid_response_callback_exhausts_stream_before_correction(self):
        from summary_response import SummaryValidationError
        calls=[];exhausted=[]
        async def stream(**kw):
            calls.append(kw)
            yield NS(completion_text=None,raw_completion={'choices':[{'finish_reason':'stop'}]}) if len(calls)==1 else valid()
            exhausted.append(len(calls))
        def save(response):
            if not isinstance(response.completion_text,str):raise SummaryValidationError('response_text_missing','$.completion_text')
        result=await summary_model_output(NS(text_chat_stream=stream),{'prompt':'unchanged material'},lambda:True,{},800,on_response=save)
        self.assertEqual(result[2],'complete');self.assertEqual(exhausted,[1,2])
        self.assertEqual(calls[0]['prompt'],calls[1]['prompt'])
        self.assertIn('$.completion_text',calls[1]['system_prompt'])

    async def test_capacity_retry_keeps_all_97_materials_without_budget_growth(self):
        import json
        calls=[];metrics={};materials=[{'event':str(i),'text':'待合并材料'+str(i)} for i in range(97)]
        request={'prompt':json.dumps({'materials':materials}),'system_prompt':'original policy','contexts':[]}
        async def stream(**kw):
            calls.append(kw)
            text='超长材料'*401 if len(calls)==1 else '97条材料中的必要认知已按原始条件合并。'
            yield NS(completion_text=json.dumps({'summary':text}),raw_completion={'choices':[{'finish_reason':'stop'}]})
        result=await summary_model_output(NS(text_chat_stream=stream),request,lambda:True,metrics,1500)
        self.assertEqual(len(calls),2);self.assertEqual(metrics['summaryCapacityRetries'],1)
        self.assertEqual(calls[0]['prompt'],calls[1]['prompt']);self.assertEqual(json.loads(calls[1]['prompt'])['materials'],materials)
        self.assertEqual(calls[0]['contexts'],calls[1]['contexts']);self.assertNotIn('max_tokens',calls[1])
        self.assertLessEqual(len(result[2]),1500)
        self.assertIn('不超过1500字符',calls[1]['system_prompt'])
    async def test_repeated_capacity_failure_does_not_truncate_or_make_third_call(self):
        import json
        calls=[]
        async def stream(**kw):
            calls.append(kw)
            yield NS(completion_text=json.dumps({'summary':'原始信息'*401}),raw_completion={'choices':[{'finish_reason':'stop'}]})
        with self.assertRaisesRegex(ValueError,'summary_output_capacity_exceeded_material_retained'):
            await summary_model_output(NS(text_chat_stream=stream),{},lambda:True,{},1500)
        self.assertEqual(len(calls),2)
    async def test_disconnect_closes_stream_and_uses_same_request_once(self):
        calls=[];closed=[];metrics={};request={'prompt':'original','request_max_retries':0}
        async def stream(**kw):
            calls.append(('stream',kw))
            try:
                yield NS(is_chunk=True)
                raise OSError('read interrupted')
            finally:closed.append(True)
        async def chat(**kw):calls.append(('chat',kw));return valid()
        result=await summary_model_output(NS(text_chat_stream=stream,text_chat=chat),request,lambda:True,metrics,800)
        self.assertEqual(result[2],'complete');self.assertEqual(calls,[('stream',request),('chat',request)])
        self.assertEqual(closed,[True]);self.assertEqual(metrics['summaryNonstreamFallbacks'],1)

    async def test_hosted_empty_stream_uses_existing_nonstream_recovery(self):
        # behavior.text_stream reports EOF before a final response with this
        # error; the lower helper never gets to raise its ValueError variant.
        calls=[]
        async def stream(**kw):
            calls.append('stream')
            raise RuntimeError('incomplete_model_stream')
            yield
        async def chat(**kw):calls.append('chat');return valid()
        result=await summary_model_output(NS(text_chat_stream=stream,text_chat=chat),{},lambda:True,{},800)
        self.assertEqual(result[2],'complete');self.assertEqual(calls,['stream','chat'])

    async def test_upstream_stream_errors_try_same_bound_nonstream_request(self):
        import httpx
        from openai import APIError,InternalServerError
        http_request=httpx.Request('POST','https://summary.example/v1/chat/completions')
        errors=(APIError('stream failed',http_request,body={'type':'server_error'}),
                InternalServerError('stream failed',response=httpx.Response(502,request=http_request),body=None))
        request={'prompt':'complete original batch','request_max_retries':0}
        for error in errors:
            with self.subTest(error=type(error).__name__):
                calls=[];metrics={}
                async def stream(**kw):
                    try:raise error
                    finally:calls.append(('closed',kw))
                    yield
                async def chat(**kw):
                    calls.append(('chat',kw));return valid()
                result=await summary_model_output(NS(text_chat_stream=stream,text_chat=chat),request,lambda:True,metrics,800)
                self.assertEqual(result[2],'complete')
                self.assertEqual(calls,[('closed',request),('chat',request)])
                self.assertEqual(metrics['summaryRequests'],2)

    async def test_read_deadline_closes_stream_before_fallback(self):
        from unittest.mock import patch
        calls=[];closed=[]
        async def stream(**kw):
            calls.append('stream')
            try:
                await asyncio.Event().wait()
                yield
            finally:closed.append(True)
        async def chat(**kw):
            self.assertEqual(closed,[True]);calls.append('chat');return valid()
        with patch('summary_response.SUMMARY_REQUEST_TIMEOUT_SECONDS',-4.99):
            result=await summary_model_output(NS(text_chat_stream=stream,text_chat=chat),{},lambda:True,{},800)
        self.assertEqual(result[2],'complete');self.assertEqual(calls,['stream','chat'])

    async def test_invalid_nonstream_result_does_not_start_third_request(self):
        calls=[]
        async def stream(**kw):
            calls.append('stream');raise OSError('read interrupted')
            yield
        async def chat(**kw):calls.append('chat');return NS(completion_text='invalid',raw_completion={'choices':[{'finish_reason':'stop'}]})
        with self.assertRaises(ValueError):await summary_model_output(NS(text_chat_stream=stream,text_chat=chat),{},lambda:True,{},800)
        self.assertEqual(calls,['stream','chat'])

    async def test_configuration_change_during_fallback_discards_response(self):
        live=True
        async def stream(**kw):
            raise OSError('read interrupted')
            yield
        async def chat(**kw):
            nonlocal live
            live=False;return valid()
        self.assertIsNone(await summary_model_output(NS(text_chat_stream=stream,text_chat=chat),{},lambda:live,{},800))

    async def test_cancellation_during_fallback_does_not_retry(self):
        started=asyncio.Event();calls=[]
        async def stream(**kw):
            calls.append('stream');raise OSError('read interrupted')
            yield
        async def chat(**kw):calls.append('chat');started.set();await asyncio.Event().wait()
        task=asyncio.create_task(summary_model_output(NS(text_chat_stream=stream,text_chat=chat),{},lambda:True,{},800))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(calls,['stream','chat'])

if __name__=='__main__':unittest.main()
