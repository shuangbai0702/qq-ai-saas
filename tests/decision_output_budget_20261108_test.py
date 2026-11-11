"""Real SDK final HTTP budget, isolated and entirely in-memory MockTransport."""
import asyncio,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
temporary=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=temporary.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
import httpx,autonomy
from behavior import install_runtime
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial


class Budget(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_uses_task_budget_without_changing_provider_or_another_task(self):
        requests=[]
        async def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json={'id':'offline','model':'offline','choices':[{'index':0,'message':{'role':'assistant','content':'{"contact":false,"topic":""}'},'finish_reason':'stop'}]})
        old=ProviderOpenAIOfficial._create_http_client
        ProviderOpenAIOfficial._create_http_client=lambda self,c:httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runtime=install_runtime(NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={})),{})
        provider=ProviderOpenAIOfficial({'id':'offline','type':'openai_chat_completion','model':'offline','key':['fixture'],'api_base':'https://fixture.invalid/v1','custom_extra_body':{'max_tokens':4096}}, {})
        try:
            token=autonomy.decision_token_limit.set(autonomy.DECISION_OUTPUT_TOKENS)
            try:await provider.text_chat(prompt='structured decision',contexts=[],func_tool=None)
            finally:autonomy.decision_token_limit.reset(token)
            await provider.text_chat(prompt='normal',contexts=[],func_tool=None)
            self.assertEqual(requests[0].get('max_tokens'),autonomy.DECISION_OUTPUT_TOKENS)
            self.assertEqual(requests[1].get('max_tokens'),4096)
            self.assertEqual(len(requests),2,'no automatic repair/model retry')
            self.assertIsNone(autonomy.decision_token_limit.get())
            provider.provider_config['custom_extra_body']={'max_completion_tokens':4096}
            token=autonomy.decision_token_limit.set(autonomy.DECISION_OUTPUT_TOKENS)
            try:await provider.text_chat(prompt='structured completion-token decision',contexts=[],func_tool=None)
            finally:autonomy.decision_token_limit.reset(token)
            self.assertEqual(requests[-1]['max_completion_tokens'],autonomy.DECISION_OUTPUT_TOKENS)
            self.assertNotIn('max_tokens',requests[-1],'do not send conflicting provider limits')
            self.assertEqual(provider.provider_config['custom_extra_body'],{'max_completion_tokens':4096})
            # The owner-only headroom must survive the real SDK's final merge,
            # including providers using max_completion_tokens.
            for key in ('max_tokens','max_completion_tokens'):
                provider.provider_config['custom_extra_body']={key:4096}
                token=autonomy.decision_token_limit.set(autonomy.OWNER_DECISION_OUTPUT_TOKENS)
                try:await provider.text_chat(prompt='owner decision',contexts=[],func_tool=None)
                finally:autonomy.decision_token_limit.reset(token)
                self.assertEqual(requests[-1][key],8192)
                self.assertNotIn('max_completion_tokens' if key=='max_tokens' else 'max_tokens',requests[-1])
                self.assertEqual(provider.provider_config['custom_extra_body'],{key:4096})
        finally:
            await provider.client.close();runtime['close']();ProviderOpenAIOfficial._create_http_client=old


if __name__=='__main__':
    try:unittest.main()
    finally:temporary.cleanup()
