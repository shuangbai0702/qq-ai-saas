"""No network: exercise the actual authenticated pre-dispatch bridge helper."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import external_action_budget as budget

class BudgetBridge(unittest.IsolatedAsyncioTestCase):
    async def test_missing_bridge_fails_closed(self):
        with patch.object(budget,'manifest',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'bridge_unavailable'):
                await budget.update({}, {'id':'work','cycleId':'cycle','epoch':2},'logical','reserve')

    async def test_legacy_identity_does_not_invent_cycle(self):
        with patch.object(budget,'manifest',side_effect=AssertionError('unexpected bridge')):
            await budget.update({}, {'id':'legacy'},'logical','reserve')

    async def test_authenticated_authority_request_and_rejection(self):
        calls=[]
        class Response:
            status=200
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def json(self):return {'reserved':True}
        class Client:
            def __init__(self,**kwargs):calls.append(kwargs)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def post(self,url,**kwargs):calls.append((url,kwargs));return Response()
        bridge={'url':'http://127.0.0.1:1','botId':'bot','token':'fixture-only'}
        with patch.object(budget,'manifest',return_value=bridge),patch.object(budget.aiohttp,'ClientSession',Client):
            await budget.update({}, {'id':'work','cycleId':'cycle','epoch':2,'budget':{'externalActions':999}},'logical','reserve')
            self.assertFalse(calls[0]['trust_env'])
            self.assertEqual(calls[0]['headers'],{'Authorization':'Bearer fixture-only'})
            self.assertEqual(calls[1][1]['json'],{'workId':'work','epoch':2,'logicalActionId':'logical','phase':'reserve'})
            self.assertFalse(calls[1][1]['allow_redirects'])
            Response.status=409
            with self.assertRaisesRegex(RuntimeError,'budget_rejected'):
                await budget.update({}, {'id':'work','cycleId':'cycle','epoch':2},'logical','reserve')

    async def test_failed_settlement_cannot_mask_success_or_refund(self):
        with patch.object(budget,'update',side_effect=asyncio.TimeoutError):
            await budget.settle({}, {'id':'work','cycleId':'cycle'},'logical','confirmed')

if __name__=='__main__':unittest.main()
