"""Run one owned named Cloudflare tunnel; never launch a business service."""
import argparse,importlib.util,json,os,time,urllib.request,contextlib,traceback
from pathlib import Path

def tunnel_command(config):
    for key in ('sourceRoot','stateRoot','cloudflared','tokenFile'):
        if not Path(config[key]).is_absolute():raise ValueError('absolute path required: '+key)
    if not Path(config['tokenFile']).is_file():raise ValueError('tunnel token file is missing')
    return [config['cloudflared'],'tunnel','--no-autoupdate','--protocol','http2','run','--token-file',config['tokenFile']]

def run(config):
    command=tunnel_command(config);root=Path(config['sourceRoot']);state=Path(config['stateRoot']);state.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('fixed_tunnel_supervisor',root/'scripts/windows-production-supervisor.py');supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)
    import msvcrt
    with (state/'tunnel.lock').open('a+b') as lock:
        lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        job=supervisor.WindowsJob();job.assign()
        service=supervisor.Service('fixed-web-tunnel',command,root,dict(os.environ),state,state,3)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            while True:
                now=time.monotonic();service.reap(now)
                try:
                    with opener.open('http://127.0.0.1:8787/v1/health',timeout=5) as response:
                        info=json.load(response);healthy=response.status==200 and info.get('ok') is True and info.get('publicOrigin')=='https://shuangbai.online'
                except (OSError,ValueError):healthy=False
                if healthy and service.process is None and now>=service.next:service.start()
                supervisor.atomic(state/'state.json',{'pid':os.getpid(),'tunnelPid':service.process.pid if service.process else None,'originHealthy':healthy,'websiteOnly':True,'checkedAt':time.time()})
                time.sleep(5)
        finally:service.stop();job.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'));state=Path(config['stateRoot']);state.mkdir(parents=True,exist_ok=True)
    with (state/'runner.log').open('a',encoding='utf-8',buffering=1) as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        try:run(config)
        except Exception:traceback.print_exc();raise SystemExit(1)
