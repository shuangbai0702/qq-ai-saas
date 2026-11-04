"""Assert the native AstrBot SDK really serializes configured reasoning effort."""
import asyncio,json,os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
temporary=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=temporary.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
import httpx
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

async def main():
    config=json.loads(Path(sys.argv[1]).read_text())['provider'][0]
    config['key']=['fixture-key'];config['api_base']='https://fixture.invalid/v1'
    requests=[]
    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'fixture','model':config['model'],'choices':[{'index':0,'message':{'role':'assistant','content':'OK'},'finish_reason':'stop'}]})
    ProviderOpenAIOfficial._create_http_client=lambda self,c:httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider=ProviderOpenAIOfficial(config,{})
    try:
        await provider.text_chat(prompt='fixture',contexts=[],request_max_retries=0)
        assert len(requests)==1 and requests[0]['reasoning_effort']=='max'
        print('native reasoning_effort=max serialized')
    finally:await provider.client.close()

try:asyncio.run(main())
finally:temporary.cleanup()
