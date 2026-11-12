"""Streaming compression and bounded pre-commit retry, without network requests."""
import asyncio,contextvars,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parent)]
from memory_reflection import summary_model_output
from summary_stream_fixture import summary_provider
from semantic_batch_summary_test import SemanticSummary

class StreamingSummary(SemanticSummary):
 async def run_summary(self):
  async def stream(**kw):
   yield NS(is_chunk=True,completion_text='untrusted partial')
   async for response in summary_provider(self.chat).text_chat_stream(**kw):yield response
  self.r.binding=({'memory':True,'memorySummary':True,'naturalConversation':False},NS(text_chat_stream=stream),lambda:self.live,self.metrics,contextvars.ContextVar('stream_summary',default=None))
  if not self.r.retry():return False
  await self.r.task
  return True
 async def test_invalid_first_response_retries_once_and_commits_once(self):
  self.record();original=self.chat
  async def chat(**kw):
   response=await original(**kw)
   if len(self.calls)==1:response.completion_text='not json'
   return response
  self.chat=chat
  await self.run_summary()
  self.assertEqual(len(self.calls),2)
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summaries').fetchone()[0],1)
  self.assertEqual(self.metrics['summaryImmediateRetries'],1)
 async def test_second_network_failure_resets_final_audit(self):
  self.record();original=self.chat
  async def chat(**kw):
   response=await original(**kw)
   if len(self.calls)==1:response.completion_text='not json'
   else:raise OSError('fixture network failure')
   return response
  self.chat=chat
  await self.run_summary()
  row=self.db.execute('SELECT reason,decode_status FROM memory_operation_audits').fetchone()
  self.assertEqual(row,('request_failed','not_received'))
  self.assertIsNone(self.checkpoint())
 async def test_commit_failure_does_not_repeat_model_request(self):
  self.record()
  self.db.execute("CREATE TRIGGER reject_summary BEFORE INSERT ON memory_summaries BEGIN SELECT RAISE(ABORT,'fixture commit failure'); END")
  self.db.commit()
  await self.run_summary()
  self.assertEqual(len(self.calls),1)
  self.assertIsNone(self.checkpoint())
  self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)

del SemanticSummary

class StreamRead(unittest.IsolatedAsyncioTestCase):
 async def test_failures_retry_exactly_twice_and_close_partial_streams(self):
  for mode in ('disconnect','no_final','json','truncated','empty','capacity'):
   with self.subTest(mode=mode):
    calls=[];closed=[];metrics={}
    async def stream(**kw):
     calls.append(kw)
     try:
      yield NS(is_chunk=True,completion_text='{"summary":"partial"}')
      if mode=='disconnect':raise OSError('fixture disconnect')
      if mode=='no_final':return
      value='not json' if mode=='json' else json.dumps({'summary':'' if mode=='empty' else 'x'*801 if mode=='capacity' else 'valid'})
      yield NS(is_chunk=False,completion_text=value,raw_completion={'choices':[{'finish_reason':'length' if mode=='truncated' else 'stop'}]})
     finally:closed.append(True)
    with self.assertRaises(Exception):await summary_model_output(NS(text_chat_stream=stream),{'request_max_retries':0},lambda:True,metrics,800)
    self.assertEqual(len(calls),2);self.assertEqual(len(closed),2)
    self.assertEqual(metrics['summaryRequests'],2)
 async def test_cancellation_closes_stream_without_retry(self):
  started=asyncio.Event();closed=[];calls=[]
  async def stream(**kw):
   calls.append(kw)
   try:
    started.set();await asyncio.Event().wait()
    yield NS(is_chunk=False)
   finally:closed.append(True)
  task=asyncio.create_task(summary_model_output(NS(text_chat_stream=stream),{},lambda:True,{},800))
  await started.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(len(calls),1);self.assertEqual(len(closed),1)
 async def test_stale_after_partial_never_retries_or_accepts_final(self):
  live=True;calls=[];closed=[]
  async def stream(**kw):
   nonlocal live
   calls.append(kw)
   try:
    live=False
    yield NS(is_chunk=False,completion_text='{"summary":"stale"}')
   finally:closed.append(True)
  result=await summary_model_output(NS(text_chat_stream=stream),{},lambda:live,{},800)
  self.assertIsNone(result);self.assertEqual(len(calls),1);self.assertEqual(len(closed),1)

if __name__=='__main__':unittest.main()
