"""Version-pinned hiro method hooks: publish actual PNG/status, never parse ASCII QR."""
import sys,os,json,base64,asyncio,time,builtins
from pathlib import Path
source,root=sys.argv[1],Path(sys.argv[2]).resolve()
sys.path.insert(0,str(Path(source)/'src'))
os.chdir(root)
def publish(state,**extra):
    target=root/'login-state.json';temp=root/'login-state.tmp'
    temp.write_text(json.dumps({'state':state,'updatedAt':time.time(),**extra}),encoding='utf8');os.replace(temp,target)
publish('starting')
from lagrange.client.base import BaseClient
from lagrange.client.client import Client
fetch_original=BaseClient.fetch_qrcode
poll_original=BaseClient.get_qrcode_result
register_original=Client.register
async def fetch(self):
    ret=await fetch_original(self)
    if isinstance(ret,tuple):publish('waiting_scan',qr='data:image/png;base64,'+base64.b64encode(ret[0]).decode())
    else:publish('failed',message='QR请求失败：'+str(ret))
    return ret
async def poll(self):
    result=await poll_original(self)
    old=json.loads((root/'login-state.json').read_text(encoding='utf8'))
    states={0:'authenticating',17:'expired',48:'waiting_scan',53:'waiting_confirm',54:'canceled'}
    publish(states.get(int(result),'failed'),**{k:old[k] for k in ['qr'] if k in old})
    return result
async def register(self):
    ok=await register_original(self)
    if ok:publish('online',uin=str(self.uin))
    return ok
def no_input(prompt=''):
    publish('verification_required',message='协议要求额外验证，需扩展网页挑战提交接口；已禁止阻塞终端input')
    raise RuntimeError('Interactive verification unavailable')
BaseClient.fetch_qrcode=fetch;BaseClient.get_qrcode_result=poll;Client.register=register
builtins.input=no_input
from euleronebot import Adapter,LagrangeProtocol,apply_patches,setup
set_online_original=LagrangeProtocol.set_online
def set_online(self,online):
    set_online_original(self,online)
    if online:publish('online',uin=str(self._self_id()))
    else:
        try:previous=json.loads((root/'login-state.json').read_text(encoding='utf8')).get('state')
        except(OSError,ValueError):previous=None
        if previous not in ('failed','expired','canceled','verification_required'):
            publish('authenticating',message='QQ已断线，协议正在恢复连接')
LagrangeProtocol.set_online=set_online
async def main():
    _,cfg=setup();apply_patches();protocol=LagrangeProtocol(cfg,Adapter(impls=cfg.connections,access_token=cfg.access_token))
    try:await protocol.run()
    except Exception as e:publish('failed',message=type(e).__name__);raise
asyncio.run(main())
