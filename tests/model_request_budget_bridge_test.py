"""Real local HTTP bridge shape and ownership; no paid inference or production."""
import asyncio,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from model_request_budget import bind


class Bridge(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_reserve_and_stale_owner_settlement(self):
        calls=[];live=True
        async def handler(request):
            self.assertEqual(request.headers['Authorization'],'Bearer fixture')
            calls.append(await request.json());return web.json_response({'status':'reserved'})
        app=web.Application();app.router.add_post('/internal/v1/bots/bot/model-request-budget',handler)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        try:
            bridge={'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]),'botId':'bot','token':'fixture'}
            with patch('model_request_budget.manifest',return_value=bridge):
                transition=bind({}, {'cycleId':'cycle','id':'action','epoch':7},lambda:live)
                await transition('request','reserve',16000);live=False
                with self.assertRaises(asyncio.CancelledError):await transition('new','reserve',16000)
                await transition('request','settled',99)
            self.assertEqual(calls,[{'workId':'action','epoch':7,'requestId':'request','phase':'reserve','source':'main','tokens':16000},{'workId':'action','epoch':7,'requestId':'request','phase':'settled','source':'main','tokens':99}])
        finally:await runner.cleanup()

    async def test_cycle_fails_closed_but_foreground_has_no_new_budget_authority(self):
        self.assertIsNone(bind({}, {'id':'foreground'},lambda:True))
        with patch('model_request_budget.manifest',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'bridge_unavailable'):
                await bind({}, {'cycleId':'cycle','id':'action','epoch':7},lambda:True)('request','reserve',12)


if __name__=='__main__':unittest.main()
