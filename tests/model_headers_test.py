"""Final SDK egress identity; no network or model requests."""
import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import httpx
from model_egress import GuardedTransport,dsh_user_agent

class Headers(unittest.IsolatedAsyncioTestCase):
    def test_version_validation(self):
        for version,expected in [('', '0.2.0-rc.2'),('0.3.1','0.3.1'),(' 0.3.1 ','0.3.1'),
                                 ('0.3.1\r\nX-Other: injected','0.2.0-rc.2'),('１.２.３','0.2.0-rc.2'),
                                 ('1.2.3-'+'a'*100,'0.2.0-rc.2')]:
            with self.subTest(version=version),patch.dict(os.environ,{'FISHBOT_DSH_HARNESS_VERSION':version}):
                self.assertEqual(dsh_user_agent(),f'deepseek-harness/{expected} (+https://github.com/deepseek-ai/deepseek-harness)')

    async def test_final_headers_are_captured_and_context_bytes_survive_retries(self):
        body=' {"model":"fixture","messages":[{"role":"system","content":"fixed"},{"role":"user","content":[{"type":"text","text":"来源与时间"},{"type":"image_url","image_url":{"url":"data:image/png;base64,AA=="}}]},{"role":"assistant","tool_calls":[{"id":"call_1","type":"function","function":{"name":"echo","arguments":"{}"}}]},{"role":"tool","tool_call_id":"call_1","content":"真实回执"}]}\n'.encode()
        observed=[];captured=[];sent=[]
        def observe(request):observed.append((request.content,request.headers['user-agent']))
        def capture(request):captured.append(request.headers['user-agent'])
        async def send(_,request):
            sent.append((request.content,request.headers['user-agent'],request.headers['authorization'],str(request.url)))
            return httpx.Response(200,stream=httpx.ByteStream(b'{}'))
        transport=GuardedTransport()
        try:
            with patch.dict(os.environ,{'FISHBOT_DSH_HARNESS_VERSION':''}),patch('model_cache.observe_wire_request',side_effect=observe),patch('model_cache.transport_started',return_value=None),patch('model_request_capture.begin',side_effect=capture),patch.object(httpx.AsyncHTTPTransport,'handle_async_request',send):
                async with httpx.AsyncClient(transport=transport,headers={'User-Agent':'SDK/default'}) as client:
                    for agent in ('override/1.0','override/2.0'):
                        await client.post('https://fixture.invalid/v1/chat/completions',content=body,headers={'User-Agent':agent,'Authorization':'Bearer fixture'})
                expected=dsh_user_agent()
            self.assertEqual(len(sent),2)
            self.assertEqual(observed,[(body,expected)]*2)
            self.assertEqual(captured,[expected]*2)
            for raw,agent,auth,url in sent:
                self.assertEqual(raw,body);self.assertEqual(agent,expected)
                self.assertEqual(auth,'Bearer fixture');self.assertEqual(url,'https://fixture.invalid/v1/chat/completions')
        finally:await transport.aclose()

if __name__=='__main__':unittest.main()
