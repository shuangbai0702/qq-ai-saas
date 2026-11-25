"""Real aiocqhttp/Quart socket overlap, including an API round trip."""
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))
from aiocqhttp import CQHttp
from native_ingress import bind_api_connection, bind
from types import SimpleNamespace


class Connections(unittest.IsolatedAsyncioTestCase):
    async def test_non_onebot_platform_keeps_its_native_conversion(self):
        message = SimpleNamespace()
        async def convert(raw):return message
        platform = SimpleNamespace(convert_message=convert)
        bind(platform, lambda _: lambda: None)
        raw = {'post_type': 'message', 'message': 'hello'}
        self.assertIs(await platform.convert_message(raw), message)
        self.assertIs(message.raw_message, raw)

    async def overlap(self, bot, *, role='Universal'):
        client = bot.server_app.test_client()
        connected = asyncio.Queue()
        register = bot._add_wsr_api_client
        def registered():
            register()
            connected.put_nowait(None)
        bot._add_wsr_api_client = registered
        headers = {'X-Self-ID': '91000001', 'X-Client-Role': role,
                   'Authorization': 'Bearer synthetic-connection-test'}
        async with AsyncExitStack() as cleanup:
            old = await cleanup.enter_async_context(AsyncExitStack())
            old_socket = await old.enter_async_context(client.websocket('/ws/', headers=headers))
            await old_socket.send_json({})
            await asyncio.wait_for(connected.get(), 3)
            first = bot._wsr_api_clients['91000001']
            async with client.websocket('/ws/', headers=headers) as new_socket:
                await new_socket.send_json({})
                await asyncio.wait_for(connected.get(), 3)
                replacement = bot._wsr_api_clients['91000001']
                self.assertIsNot(first, replacement)
                await old.aclose()
                self.assertIs(bot._wsr_api_clients['91000001'], replacement)
                async with client.websocket('/ws/', headers={**headers, 'X-Self-ID': '91000002'}):
                    await asyncio.wait_for(connected.get(), 3)
                    peer = bot._wsr_api_clients['91000002']
                    call = asyncio.create_task(bot.get_status(self_id='91000001'))
                    try:
                        request = await asyncio.wait_for(new_socket.receive_json(), 3)
                        self.assertEqual(request['action'], 'get_status')
                        await new_socket.send_json({'status': 'ok', 'retcode': 0,
                            'data': {'online': True}, 'echo': request['echo']})
                        self.assertEqual(await asyncio.wait_for(call, 3), {'online': True})
                        self.assertIs(bot._wsr_api_clients['91000002'], peer)
                    finally:
                        if not call.done():
                            call.cancel()
                            await asyncio.gather(call, return_exceptions=True)
                self.assertNotIn('91000002', bot._wsr_api_clients)
            self.assertNotIn('91000001', bot._wsr_api_clients)

    async def test_owned_cleanup_for_api_only_socket(self):
        bot = CQHttp(access_token='synthetic-connection-test', api_timeout_sec=2)
        bind_api_connection(bot)
        await self.overlap(bot, role='API')

    async def test_owned_cleanup_keeps_replacement_and_real_api_routing(self):
        bot = CQHttp(access_token='synthetic-connection-test', api_timeout_sec=2)
        bind_api_connection(bot)
        bind_api_connection(bot)
        await self.overlap(bot)


if __name__ == '__main__':
    unittest.main()
