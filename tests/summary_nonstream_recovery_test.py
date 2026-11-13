"""Bounded same-provider recovery for read-only checkpoint requests."""
import asyncio,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from memory_reflection import summary_model_output

def valid():
    return NS(completion_text='{"summary":"complete"}',raw_completion={'choices':[{'finish_reason':'stop'}]})

class Recovery(unittest.IsolatedAsyncioTestCase):
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
