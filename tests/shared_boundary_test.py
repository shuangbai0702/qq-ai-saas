"""Offline boundary regressions. No worker startup, sockets or real message sends."""
import ast,asyncio,copy,json,sys
from pathlib import Path
from types import SimpleNamespace

root=Path(__file__).resolve().parents[1]
worker=ast.parse((root/'adapters/astrbot/shared-worker.py').read_text(encoding='utf8'))
validate=next(n for n in worker.body if isinstance(n,ast.FunctionDef) and n.name=='validate_member')
namespace={}
exec(compile(ast.Module(body=[validate],type_ignores=[]),'<worker-validator>','exec'),namespace)
body=json.load(sys.stdin)
namespace['validate_member']('bot-A',body)
invalid=[None,[],True,{},dict(body,generation=True),dict(body,revision=-1),dict(body,prompt='x'*20001),dict(body,key=[]),dict(body,runtimeRoot=None),dict(body,groups=['no']),dict(body,groups=['12345']*101),dict(body,config=[]),dict(body,platform=[]),dict(body,behaviorConfig=[])]
for keys,value in [(('config','agent_runner'),[]),(('config','agent_runner','config'),None),(('config','agent_runner','config','persona'),[]),(('config','provider'),[None]),(('config','provider'),[]),(('config','provider',0,'key'),'secret'),(('platform','ws_reverse_port'),True),(('behaviorConfig','behavior'),[]),(('config','dashboard'),False)]:
    b=copy.deepcopy(body);target=b
    for key in keys[:-1]:target=target[key]
    target[keys[-1]]=value;invalid.append(b)
b=copy.deepcopy(body);deep={};b['config']['extra']=deep
for _ in range(18):deep['next']={};deep=deep['next']
invalid.append(b)
invalid.append(dict(body,config=dict(body['config'],extra=[0]*257)))
# Exercise the real PUT handler without binding an HTTP server. Invalid input must
# be rejected before unload/install, including an already active member.
main=next(n for n in worker.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='main')
put=next(n for n in main.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='put')
from aiohttp.web import HTTPBadRequest as BadRequest
class Request:
    match_info={'id':'bot-A'}
    def __init__(self,value):self.value=value
    async def json(self):return self.value
async def run():
    calls=[]
    async def changed(*args):calls.append(args)
    namespace['stop_fence']=SimpleNamespace(quiescing=False)
    namespace.update(web=SimpleNamespace(HTTPBadRequest=BadRequest),lock=asyncio.Lock(),members={'bot-A':body},unload=changed,install=changed)
    exec(compile(ast.Module(body=[put],type_ignores=[]),'<worker-put>','exec'),namespace)
    for value in invalid:
        try:await namespace['put'](Request(value))
        except BadRequest:pass
        else:raise AssertionError(f'Accepted malformed member: {value!r}')
    assert not calls,'Malformed request changed a live member'
    no_key=copy.deepcopy(body);no_key['key']='';no_key['config']['provider']=[]
    namespace['validate_member']('bot-A',no_key)
    # Stub only upstream tool classes: delegation is recorded, never sent.
    import types
    module=types.ModuleType('astrbot.core.tools.message_tools')
    forwarded=[]
    class Tool:
        async def call(self,context,**kwargs):forwarded.append(kwargs);return 'forwarded'
    class Send(Tool):pass
    class History(Tool):pass
    module.SendMessageToUserTool=Send;module.GetGroupMessageHistoryTool=History
    sys.modules['astrbot.core.tools.message_tools']=module
    sys.path.insert(0,str(root/'adapters/astrbot'))
    from tenant_tools import install
    install({'bot-A':{'generation':1,'revision':2}})
    work=SimpleNamespace(confirmed=0,attempted=0)
    async def send(chain):forwarded.append(chain);work.attempted+=1;work.confirmed+=1
    extra={'_hosted_lease':{'generation':1,'revision':2},'_hosted_pipeline':work}
    ctx=SimpleNamespace(context=SimpleNamespace(event=SimpleNamespace(get_platform_id=lambda:'bot-A',get_extra=lambda key:extra.get(key),send=send)))
    for messages in [None,{},'text',[],[None],['text'],[{'type':[]}],[{'type':'plain','text':{}}],[{'type':'plain','text':'x'*20001}],[{'type':'plain','text':'ok'}]*101,[{'type':'unknown'}],[{'type':'image'}],[{'type':'plain','text':'x','extra':'x'}]]:
        assert (await Send().call(ctx,messages=messages)).startswith('error:')
    assert not forwarded
    from unittest.mock import patch
    async def download(url,path,**kwargs):Path(path).write_bytes(b'fixture-media')
    with patch('media_egress.download_file',side_effect=download):
        for kind,fields in [('plain',{'text':'hello'}),('mention_user',{'mention_user_id':'12345'}),*[(k,{'url':'https://example.com/media'}) for k in ('image','record','video','file')]]:
            assert json.loads(await Send().call(ctx,messages=[{'type':kind,**fields}]))['status']=='confirmed'
    assert work.confirmed==6,'All valid native sends must pass the owned event/OutputPlan path'
    count=len(forwarded)
    assert 'local file' in await Send().call(ctx,messages=[{'type':'file','path':'/private/key'}])
    for kind in ('image','record','video','file'):
        for url in ('http://127.0.0.1/a','http://169.254.169.254/a','http://10.0.0.1/a','file:///etc/passwd','file:///C:/Windows/win.ini','/etc/passwd','C:\\Windows\\win.ini','\\\\server\\share\\private','data:text/plain,secret','ftp://example.com/a','https:///missing-host','https://user:pass@example.com/a','https://example.com:bad/a','https://example.com/\nprivate'):
            assert (await Send().call(ctx,messages=[{'type':kind,'url':url}])).startswith('error:'),f'Accepted unsafe {kind} URL {url!r}'
    assert len(forwarded)==count
    assert 'cross-bot' in await Send().call(ctx,session='bot-B:GroupMessage:12345',messages=[{'type':'plain','text':'hello'}])
    assert 'invalid session' in await Send().call(ctx,session={},messages=[{'type':'plain','text':'hello'}])
    ctx.context.event.get_extra=lambda key:{'generation':1,'revision':1}
    assert 'stale' in await Send().call(ctx,messages=[{'type':'plain','text':'old'}])
    assert 'stale' in await History().call(ctx,limit=5)
    assert len(forwarded)==count
    print(f'PASS {len(invalid)} malformed PUT cases; normal generated config and message delegation; tool denials')
asyncio.run(run())
