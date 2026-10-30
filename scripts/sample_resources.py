"""Windows/Linux samples: each PID is charged to one component, never twice.
RSS is working set/resident memory, not private memory or a capacity promise.
"""
import argparse,csv,json,time,sys
from pathlib import Path
import psutil
parser=argparse.ArgumentParser()
parser.add_argument('--root',default='.local');parser.add_argument('--seconds',type=int,default=30)
parser.add_argument('--interval',type=float,default=1);parser.add_argument('--output',default='test-results/resource-samples.json')
parser.add_argument('--pid',action='append',default=[],help='Additional NAME=PID component')
parser.add_argument('--manifest',help='Explicit JSON map of component name to PID')
parser.add_argument('--cgroup-root',help='Optional read-only cgroup-v2 directory')
args=parser.parse_args();root=Path(args.root).resolve();samples=[]
def read_json(file):
    try:return json.loads(Path(file).read_text(encoding='utf-8-sig'))
    except(OSError,ValueError):return None
def components():
    result={}
    if args.manifest:
        data=read_json(args.manifest)or{};result.update({k:int(v)for k,v in data.items()if isinstance(v,int)})
    else:
        production=read_json(root/'production-process.json');dev=read_json(root/'dev-processes.json')
        if production and psutil.pid_exists(production.get('api',-1)):result['control_api_db_agent']=production['api']
        elif dev:result.update({name:dev[key]for name,key in[('web_development','web'),('control_api_db_agent','api'),('development_launcher','launcher')]if key in dev})
    for file in([]if args.manifest else(root/'bots').glob('*/*.process.json')):
        record=read_json(file)
        if record and 'pid'in record:result[f'{file.parent.name}/{file.stem}']=record['pid']
    for spec in args.pid:
        name,pid=spec.rsplit('=',1);result[name]=int(pid)
    return result
def process_info(process):
    memory=process.memory_info();full=process.memory_full_info()
    return{'pid':process.pid,'ppid':process.ppid(),'name':process.name(),'created':process.create_time(),'rssBytes':memory.rss,'ussBytes':getattr(full,'uss',None),'pssBytes':getattr(full,'pss',None),'cpuSeconds':sum(process.cpu_times()[:2])}
def cgroup_info():
    result={}
    if not args.cgroup_root:return result
    for group in[Path(args.cgroup_root),*Path(args.cgroup_root).glob('*')]:
        if not group.is_dir():continue
        values={}
        for name in['memory.current','memory.peak','memory.events','cpu.stat','pids.current']:
            try:values[name]=(group/name).read_text().strip()
            except OSError:pass
        if values:result[str(group)]=values
    return result
count=max(1,int(args.seconds/args.interval))
for index in range(count):
    roots=components();trees={};processes={}
    for name,pid in roots.items():
        try:
            process=psutil.Process(pid);members=[process,*process.children(recursive=True)];trees[name]={member.pid for member in members}
            for member in members:
                try:processes[member.pid]=process_info(member)
                except(psutil.NoSuchProcess,psutil.AccessDenied):pass
        except(psutil.NoSuchProcess,psutil.AccessDenied):pass
    processes.pop(psutil.Process().pid,None) # measurement process is not product cost
    # Most-specific registered subtree owns descendants. Bot roots override API
    # and launcher ancestors; the total below sums a unique PID set.
    owners={pid:min((name for name,tree in trees.items()if pid in tree),key=lambda name:len(trees[name]))for pid in processes}
    row={'schemaVersion':2,'time':time.time(),'platform':sys.platform,'host':{'memAvailableBytes':psutil.virtual_memory().available,'memoryTotalBytes':psutil.virtual_memory().total},'components':{},'cgroups':cgroup_info()}
    for name,tree in trees.items():
        assigned=[p for pid,p in processes.items()if owners.get(pid)==name]
        value={'pid':roots[name],'processes':len(assigned),'pids':[p['pid']for p in assigned],'rssBytes':sum(p['rssBytes']for p in assigned),'selfRssBytes':processes.get(roots[name],{}).get('rssBytes'),'rawTreeRssBytes':sum(processes[pid]['rssBytes']for pid in tree if pid in processes),'cpuSeconds':sum(p['cpuSeconds']for p in assigned)}
        for metric in['ussBytes','pssBytes']:
            values=[p[metric]for p in assigned];value[metric]=sum(values)if values and all(v is not None for v in values)else None
        row['components'][name]=value
    row['total']={'uniqueProcesses':len(processes),'rssBytes':sum(p['rssBytes']for p in processes.values()),'ussBytes':sum(p['ussBytes']for p in processes.values())if all(p['ussBytes']is not None for p in processes.values())else None,'pssBytes':sum(p['pssBytes']for p in processes.values())if all(p['pssBytes']is not None for p in processes.values())else None,'cpuSeconds':sum(p['cpuSeconds']for p in processes.values())}
    row['processes']=list(processes.values());samples.append(row)
    if index+1<count:time.sleep(args.interval)
output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(samples,indent=2),encoding='utf8')
with output.with_suffix('.csv').open('w',newline='',encoding='utf8')as stream:
    writer=csv.DictWriter(stream,fieldnames=['time','component','pid','rssBytes','ussBytes','pssBytes','cpuSeconds','processes']);writer.writeheader()
    for row in samples:
        for name,item in row['components'].items():writer.writerow({'time':row['time'],'component':name,**{key:item[key]for key in['pid','rssBytes','ussBytes','pssBytes','cpuSeconds','processes']}})
print(output)
