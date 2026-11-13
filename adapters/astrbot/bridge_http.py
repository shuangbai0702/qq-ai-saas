"""One authenticated bridge transport; callers own authorization and outcomes."""
import aiohttp

async def post(bridge,path,payload,timeout=8):
    url=bridge['url'].rstrip('/')+'/internal/v1/bots/'+bridge['botId']+'/'+path
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),trust_env=False,headers={'Authorization':'Bearer '+bridge['token']}) as client:
        async with client.post(url,json=payload,allow_redirects=False) as response:
            return response.status,await response.json()
