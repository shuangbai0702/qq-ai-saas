"""Checkpoint-only network deadline; no HTTP or paid Provider requests."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import httpx
from model_egress import GuardedTransport
from model_cache import SUMMARY_REQUEST_TIMEOUT_SECONDS

class Budget(unittest.IsolatedAsyncioTestCase):
 async def test_background_read_deadline_does_not_change_other_requests(self):
  captured=[]
  async def send(_,request):
   captured.append(dict(request.extensions['timeout']))
   return httpx.Response(200,stream=httpx.ByteStream(b'{}'))
  transport=GuardedTransport()
  try:
   for phase in ('small_summary','big_merge','foreground','group_presence','private_freshness','summary_image'):
    request=httpx.Request('POST','https://fixture.invalid/v1/chat/completions',extensions={'timeout':{'read':60,'connect':10,'pool':5,'write':60}})
    with patch('model_cache.transport_started',return_value={'attempt':{'phase':phase}}),patch('model_cache.observe_wire_request'),patch('model_cache.transport_result'),patch('model_request_capture.begin',return_value=None),patch.object(httpx.AsyncHTTPTransport,'handle_async_request',send):
     response=await transport.handle_async_request(request);await response.aclose()
    self.assertEqual(captured[-1]['read'],SUMMARY_REQUEST_TIMEOUT_SECONDS if phase in ('small_summary','big_merge') else 60)
    self.assertEqual(captured[-1]['connect'],10);self.assertEqual(captured[-1]['pool'],5)
  finally:await transport.aclose()

if __name__=='__main__':unittest.main()
