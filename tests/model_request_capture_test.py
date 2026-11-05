import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import httpx,model_request_capture as capture
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_capture_is_owned_bounded_private_and_does_not_change_bytes(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);cv=contextvars.ContextVar('event',default=None)
   event=NS(get_extra=lambda key:{'root':temp},get_platform_id=lambda:'owned',message_obj=NS(message_id='123'))
   module=NS(current_message_event=cv);req=httpx.Request('POST','https://example.com/v1/chat/completions',json={'model':'gpt-6.1-sol','messages':[{'role':'user','content':'hi'}]},headers={'Authorization':'Bearer never-save-this','X-Astrbot-Conversation-Id':'cid'})
   with patch.dict(sys.modules,{'astrbot.core.platform.astr_message_event':module}):
    self.assertIsNone(capture.begin(req));cv.set(event);self.assertIsNone(capture.begin(req))
    cfg={'botId':'owned','model':'gpt-6.1-sol','session':'a'*24,'expiresAt':time.time()+600,'maxRequests':2};arm=root/'model-request-capture.json';arm.write_text(json.dumps(cfg))
    original=req.content;c=capture.begin(req);self.assertIsNotNone(c);self.assertEqual(req.content,original)
    class Stream(httpx.AsyncByteStream):
     async def __aiter__(self):
      for x in [b'data: {"choices":[],"usage":{"prompt_tokens":3000,',b'"prompt_tokens_details":{"cached_tokens":2000}}}\n\ndata: [DONE]\n\n']:yield x
     async def aclose(self):pass
    s=capture.CaptureStream(Stream(),c);received=b''.join([x async for x in s]);await s.aclose();self.assertIn(b'cached_tokens',received)
    files=list((root/'model-request-captures'/('a'*24)).glob('*'));self.assertEqual(len(files),2)
    request=json.loads(next(p for p in files if '.request.' in p.name).read_text());self.assertNotIn('authorization',request['headers']);self.assertNotIn('never-save-this',json.dumps(request))
    response=json.loads(next(p for p in files if '.response.' in p.name).read_text());self.assertTrue(response['complete']);self.assertEqual(response['usage'][0]['prompt_tokens_details']['cached_tokens'],2000)
    self.assertIsNotNone(capture.begin(req));self.assertIsNone(capture.begin(req))
    cfg.update(session='b'*24,botId='other');arm.write_text(json.dumps(cfg));self.assertIsNone(capture.begin(req))
    cfg.update(botId='owned',expiresAt=time.time()-1);arm.write_text(json.dumps(cfg));self.assertIsNone(capture.begin(req))
 async def test_background_binding_is_private_and_fenced_at_egress(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);lease={'root':temp};settings={'owned':lease};provider=NS(provider_config={'id':'provider-owned'})
   resolve=lambda id:provider if id=='owned' else None
   cfg={'botId':'owned','model':'gpt-6-luna','session':'c'*24,'expiresAt':time.time()+600,'maxRequests':8}
   (root/'model-request-capture.json').write_text(json.dumps(cfg))
   req=httpx.Request('POST','https://example.com/v1/chat/completions',json={'model':'gpt-6-luna','messages':[]})
   cv=contextvars.ContextVar('event',default=None)
   with patch.dict(sys.modules,{'astrbot.core.platform.astr_message_event':NS(current_message_event=cv)}):
    with capture.provider_scope(provider,settings,resolve):
     self.assertIsNone(cv.get());self.assertIsNotNone(capture.begin(req))
     settings['owned']={'root':temp};self.assertIsNone(capture.begin(req));settings['owned']=lease
     token=cv.set(NS(get_extra=lambda key:None,get_platform_id=lambda:'foreign'))
     self.assertIsNone(capture.begin(req));cv.reset(token)
    self.assertIsNone(capture.begin(req))
    with capture.provider_scope(provider,settings,lambda id:object()):self.assertIsNone(capture.begin(req))
    row=json.loads(next((root/'model-request-captures'/('c'*24)).glob('*.request.json')).read_text())
    self.assertEqual(row['callKind'],'background');self.assertEqual(row['botId'],'owned');self.assertEqual(row['eventId'],'')

 async def test_concurrent_providers_cancellation_and_stream_close_restore_context(self):
  providers={id:NS(provider_config={'id':'provider-'+id}) for id in ('a','b')}
  settings={id:{'root':id} for id in providers};entered=asyncio.Event();release=asyncio.Event();seen=[]
  async def call(provider):
   seen.append(capture._provider_owner.get()[0]);entered.set();await release.wait()
   seen.append(capture._provider_owner.get()[0]);return 'ok'
  wrapped=capture.bind_provider(call,settings,providers.get)
  a=asyncio.create_task(wrapped(providers['a']));await entered.wait();entered.clear()
  b=asyncio.create_task(wrapped(providers['b']));await entered.wait();a.cancel()
  with self.assertRaises(asyncio.CancelledError):await a
  release.set();self.assertEqual(await b,'ok');self.assertEqual(seen,['a','b','b']);self.assertIsNone(capture._provider_owner.get())
  closed=[]
  async def stream(provider):
   try:
    yield capture._provider_owner.get()[0]
    raise RuntimeError('stream_failed')
   finally:closed.append(True)
  wrapped_stream=capture.bind_provider(stream,settings,providers.get)
  g=wrapped_stream(providers['a']);self.assertEqual(await anext(g),'a')
  self.assertIsNone(capture._provider_owner.get());await g.aclose()
  self.assertEqual(closed,[True]);self.assertIsNone(capture._provider_owner.get())
  with self.assertRaises(RuntimeError):
   async for item in wrapped_stream(providers['b']):pass
  self.assertIsNone(capture._provider_owner.get());self.assertEqual(len(closed),2)

if __name__=='__main__':unittest.main()
