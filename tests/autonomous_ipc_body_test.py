import ast,asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer

async def check(file,shared):
    tree=ast.parse(Path(file).read_text(encoding='utf-8'))
    handler=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='autonomous_work')
    observed=[]
    async def autonomous(id,op,data):
        observed.append((id,op,len(data['payload'])))
        return {'ok':True}
    ns={'web':web,'behavior':{'autonomous':autonomous},'stop_fence':SimpleNamespace(quiescing=False),'members':{'bot':{'generation':1,'revision':2}},'manifest':{'token':'fixture','generation':1,'revision':2}}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[handler],type_ignores=[])),file,'exec'),ns)
    app=web.Application(client_max_size=131072)
    route='/members/{id}/autonomous/{operation}' if shared else '/autonomous/{operation}'
    app.router.add_post(route,ns['autonomous_work'])
    async with TestClient(TestServer(app)) as client:
        headers={'Authorization':'Bearer fixture','X-Member-Generation':'1','X-Member-Revision':'2'}
        url='/members/bot/autonomous/' if shared else '/autonomous/'
        response=await client.post(url+'execute',json={'payload':'x'*904000},headers=headers)
        assert response.status==200,(file,response.status)
        assert observed==[('bot' if shared else 'qq','execute',904000)]
        for op,size in [('policy',140000),('execute',4*1024*1024)]:
            response=await client.post(url+op,json={'payload':'x'*size},headers=headers)
            assert response.status==413,(file,op,response.status)
        invalid={**headers,'X-Member-Revision':'3'}
        response=await client.post(url+'execute',json={'payload':'x'*904000},headers=invalid)
        assert response.status==409
        assert len(observed)==1,'rejected request reached behavior'
    print(file+': 904 KB execute accepted; ordinary/oversize/stale refused')

async def main():
    await check('adapters/astrbot/shared-worker.py',True)
    await check('adapters/astrbot/headless.py',False)
asyncio.run(main())
