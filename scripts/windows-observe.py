"""Read-only whole managed process tree observer. No Linux or QQ traffic."""
import argparse,json,time,platform
from pathlib import Path
import psutil

p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--seconds',type=int,default=60);a=p.parse_args()
root=Path(__file__).resolve().parents[1];data=root/'.local';rows=[];start=time.time()
while True:
    records=[('control',{'pid':json.loads((data/'production-process.json').read_text())['api']})]
    for f in (data/'bots').glob('*/*.process.json'):
        try:records.append((f.parent.name+':'+f.stem,json.loads(f.read_text())))
        except (ValueError,OSError):pass
    seen=set();items=[]
    for role,rec in records:
        try:
            proc=psutil.Process(rec['pid'])
            if rec.get('created') and abs(proc.create_time()-rec['created'])>.01:continue
            processes=[proc] if role=='control' else [proc,*proc.children(recursive=True)]
            values={'rss':0,'uss':0,'privateBytes':0,'cpuSeconds':0,'processes':0}
            for child in processes:
                if child.pid in seen:continue
                try:
                    mem=child.memory_full_info();cpu=child.cpu_times()
                    values['rss']+=mem.rss;values['uss']+=getattr(mem,'uss',0)
                    values['privateBytes']+=getattr(mem,'private',0);values['cpuSeconds']+=cpu.user+cpu.system;values['processes']+=1;seen.add(child.pid)
                except psutil.Error:pass
            items.append({'role':role,'pid':proc.pid,**values})
        except psutil.Error:pass
    rows.append({'time':time.time(),'elapsed':time.time()-start,'items':items})
    if time.time()-start>=a.seconds:break
    time.sleep(1)
out=root/a.output;out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps({'environment':platform.platform(),'start':start,'end':time.time(),'metrics':{'rss':'Windows working set; shared pages counted per process','uss':'psutil unique working set (not PSS)','privateBytes':'Windows committed private bytes; not working set','cpuSeconds':'cumulative per process CPU; only compare identical PIDs'},'scope':'all persisted managed process records + control self; helper subprocesses transient and not counted','rows':rows},indent=2))
print(json.dumps({'output':str(out),'samples':len(rows),'seconds':time.time()-start}))
