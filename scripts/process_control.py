"""Never operate on a PID unless its argv includes this bot's private absolute path."""
import sys,json,os,math,time
from pathlib import Path
import psutil
def loop_activity(root,processes,now=None):
    """Existing RuntimeCare telemetry proves recent loop activity, not task progress."""
    now=time.time() if now is None else now
    for file in (Path(root)/'ai/memory-activity.json',Path(root)/'memory-activity.json'):
        try:
            value=json.loads(file.read_text())
            if not isinstance(value,dict):continue
            stamp=float(value['at']);created=float(value['created'])
            if not math.isfinite(stamp) or not math.isfinite(created) or stamp<created or stamp>now+5 or now-stamp>120:continue
            source=next((p for p in processes if p.pid==value['pid']),None)
            if source is None or abs(source.create_time()-created)>=.01:continue
            work=value.get('work',{})
            if not isinstance(work,dict):continue
            if value.get('active') is not True or not any(isinstance(work.get(k),(int,float)) and not isinstance(work[k],bool) and math.isfinite(work[k]) and work[k]>0 for k in ('active','ingress','waiting','batches','background')):continue
            return {'at':stamp,'pid':source.pid,'created':created,'active':True}
        except (OSError,ValueError,KeyError,TypeError,OverflowError,psutil.Error):pass
    return None

def inspect(pid,root,created=None):
    try:
        p=psutil.Process(pid);root=os.path.normcase(os.path.abspath(root))
        valid=any((value:=os.path.normcase(os.path.abspath(arg)))==root or value.startswith(root+os.sep) for arg in p.cmdline())
        if created is not None:valid=valid and abs(p.create_time()-float(created))<.01
        if not valid:return {'valid':False}
        children=p.children(recursive=True);rss=0;count=0
        for child in [p,*children]:
            try:rss+=child.memory_info().rss;count+=1
            except psutil.NoSuchProcess:pass
        return {'valid':True,'pid':pid,'rssBytes':rss,'processCount':count,'created':p.create_time(),'loopActivity':loop_activity(root,[p,*children])}
    except psutil.NoSuchProcess:return {'valid':False}

mode=sys.argv[1]
if mode=='batch':
    rows=json.load(sys.stdin)
    print(json.dumps([{**inspect(int(r['pid']),r['root'],r.get('created')),'id':r['id'],'kind':r['kind']} for r in rows]));sys.exit(0)
pid,root=int(sys.argv[2]),os.path.normcase(os.path.abspath(sys.argv[3]))
if mode=='inspect':
    print(json.dumps(inspect(pid,root,sys.argv[4] if len(sys.argv)>4 and sys.argv[4]!='unknown' else None)));sys.exit(0)
try:
    p=psutil.Process(pid)
    valid=any((value:=os.path.normcase(os.path.abspath(arg)))==root or value.startswith(root+os.sep) for arg in p.cmdline())
    if len(sys.argv)>4 and sys.argv[4]!='unknown':valid=valid and abs(p.create_time()-float(sys.argv[4]))<0.01
    if not valid:
        if mode=='inspect': print(json.dumps({'valid':False}))
        sys.exit(0 if mode=='inspect' else 2)
    children=p.children(recursive=True)
    if mode=='inspect':
        rss=sum(x.memory_info().rss for x in [p,*children] if x.is_running())
        print(json.dumps({'valid':True,'pid':pid,'rssBytes':rss,'processCount':len(children)+1,'created':p.create_time()}))
    else:
        for x in reversed([p,*children]):
            try:x.terminate()
            except psutil.NoSuchProcess:pass
        _,alive=psutil.wait_procs([p,*children],timeout=5)
        for x in alive:
            try:x.kill()
            except psutil.NoSuchProcess:pass
        _,alive=psutil.wait_procs(alive,timeout=3)
        if alive:sys.exit(3)
except psutil.NoSuchProcess:
    if mode=='inspect':print(json.dumps({'valid':False}))
