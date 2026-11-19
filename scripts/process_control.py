"""Never operate on a PID unless its argv includes this bot's private absolute path."""
import sys,json,os,math,time
from pathlib import Path
import psutil

def windows_long_path(value):
    from ctypes import create_unicode_buffer, windll

    expand = windll.kernel32.GetLongPathNameW
    buffer = create_unicode_buffer(260)
    size = expand(value, buffer, len(buffer))
    if size >= len(buffer):
        buffer = create_unicode_buffer(size)
        size = expand(value, buffer, len(buffer))
    return os.path.normcase(buffer.value) if 0 < size < len(buffer) else value

def owns_path(root, arguments):
    paths = [os.path.normcase(os.path.abspath(arg)) for arg in arguments]
    if any(value == root or value.startswith(root + os.sep) for value in paths):
        return True
    if os.name != 'nt':
        return False
    # Service accounts can supply an 8.3 spelling for a path that the
    # protocol launcher expanded. Compare only spellings of that directory.
    root = windows_long_path(root)
    drive = os.path.splitdrive(root)[0]
    candidates = (
        windows_long_path(value) for arg, value in zip(arguments, paths)
        if os.path.isabs(arg) and os.path.splitdrive(value)[0] == drive
    )
    return any(value == root or value.startswith(root + os.sep) for value in candidates)

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

def owned_process(pid,root,created=None):
    try:
        p=psutil.Process(pid);root=os.path.normcase(os.path.abspath(root))
        if created is not None and not abs(p.create_time()-float(created))<.01:return None
        if owns_path(root,p.cmdline()):return p
    except psutil.NoSuchProcess:pass
    return None

def inspect(pid,root,created=None):
    try:
        p=owned_process(pid,root,created)
        if p is None:return {'valid':False}
        children=p.children(recursive=True);rss=0;count=0
        for child in [p,*children]:
            try:rss+=child.memory_info().rss;count+=1
            except psutil.NoSuchProcess:pass
        return {'valid':True,'pid':pid,'rssBytes':rss,'processCount':count,'created':p.create_time(),'loopActivity':loop_activity(root,[p,*children])}
    except psutil.NoSuchProcess:return {'valid':False}
    except psutil.AccessDenied:return {'valid':False,'inspectionError':'access_denied'}

def stop_tree(process):
    try: children=process.children(recursive=True)
    except psutil.NoSuchProcess: return True
    for child in reversed([process,*children]):
        try: child.terminate()
        except psutil.NoSuchProcess: pass
    _,alive=psutil.wait_procs([process,*children],timeout=5)
    for child in alive:
        try: child.kill()
        except psutil.NoSuchProcess: pass
    _,alive=psutil.wait_procs(alive,timeout=3)
    return not alive

def main():
    mode=sys.argv[1]
    if mode=='batch':
        rows=json.load(sys.stdin)
        print(json.dumps([{**inspect(int(r['pid']),r['root'],r.get('created')),'id':r['id'],'kind':r['kind']} for r in rows]));return 0
    pid,root=int(sys.argv[2]),sys.argv[3]
    created=sys.argv[4] if len(sys.argv)>4 and sys.argv[4]!='unknown' else None
    if mode=='inspect':
        print(json.dumps(inspect(pid,root,created)));return 0
    process=owned_process(pid,root,created)
    if process is None:return 2 if psutil.pid_exists(pid) else 0
    return 0 if stop_tree(process) else 3

if __name__=='__main__':
    try:sys.exit(main())
    except psutil.AccessDenied:
        print('Process access denied; check the Agent and process service identities.',file=sys.stderr)
        sys.exit(1)
