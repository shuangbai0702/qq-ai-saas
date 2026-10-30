"""Isolated fixed-cost comparison; API performs actual auth and durable CRUD before idle sampling."""
import argparse,csv,json,os,platform,statistics,subprocess,time,shutil
from pathlib import Path
import psutil
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=12);parser.add_argument('--label',default=time.strftime('%Y%m%d-%H%M%S'));parser.add_argument('--profiles-only',action='store_true');args=parser.parse_args()
out=root/'experiments/round2-platform/results'/args.label
out.mkdir(parents=True,exist_ok=False)
if(root/'dist-server/build.json').exists():shutil.copyfile(root/'dist-server/build.json',out/'compiled-source-build.json')
cases=[('node',[],'balanced'),('imports',[],'balanced'),('db',[],'balanced'),('db-tuned',[],'compact'),('api',[],'balanced'),('api',['--max-old-space-size=128'],'balanced'),('api-agent',['--max-old-space-size=128'],'balanced')]
if args.profiles_only:cases=[('api',['--max-old-space-size=128'],'balanced'),('api',['--max-old-space-size=128'],'compact'),('api-agent',['--max-old-space-size=128'],'compact'),('api-agent',[],'compact')]
summaries=[]
for index,(mode,flags,profile) in enumerate(cases):
    case=out/f'{index}-{mode}-{profile}';case.mkdir();env=dict(os.environ);env.pop('DATABASE_URL',None);env.pop('DATA_ROOT',None);env['BENCHMARK_SECONDS']=str(args.seconds);env['DB_MEMORY_PROFILE']=profile
    with (case/'stdout.log').open('w') as stdout,(case/'stderr.log').open('w') as stderr:
        started=time.monotonic();child=subprocess.Popen(['node',*flags,'experiments/round2-platform/worker.mjs',mode,str(case)],cwd=root,env=env,stdout=stdout,stderr=stderr)
        samples=[]
        while child.poll() is None:
            try:
                p=psutil.Process(child.pid);tree=[p,*p.children(recursive=True)];rss=sum(x.memory_info().rss for x in tree);uss=sum(getattr(x.memory_full_info(),'uss',0) for x in tree);cpu=sum(sum(x.cpu_times()[:2])for x in tree)
                ready=(case/'ready.json').exists();samples.append({'elapsedSeconds':time.monotonic()-started,'rssBytes':rss,'ussBytes':uss,'cpuSeconds':cpu,'ready':ready,'hostAvailableBytes':psutil.virtual_memory().available})
            except(psutil.NoSuchProcess,psutil.AccessDenied):pass
            time.sleep(.25)
            if time.monotonic()-started>90:
                child.terminate();child.wait(10);break
        code=child.wait();(case/'samples.json').write_text(json.dumps(samples,indent=2),encoding='utf8')
        if samples:
            with(case/'samples.csv').open('w',newline='')as stream:
                writer=csv.DictWriter(stream,fieldnames=samples[0].keys());writer.writeheader();writer.writerows(samples)
        idle=[s for s in samples if s['ready']][-min(20,len(samples)):]
        summary={'mode':mode,'profile':profile,'flags':flags,'exitCode':code,'rssPeakMiB':max((s['rssBytes'] for s in samples),default=0)/2**20,'rssIdleMedianMiB':statistics.median([s['rssBytes']for s in idle])/2**20 if idle else None,'ussIdleMedianMiB':statistics.median([s['ussBytes']for s in idle])/2**20 if idle else None,'cpuSeconds':samples[-1]['cpuSeconds']if samples else None}
        summaries.append(summary);print(json.dumps(summary),flush=True)
(out/'summary.json').write_text(json.dumps({'platform':platform.platform(),'python':platform.python_version(),'node':subprocess.check_output(['node','--version'],text=True).strip(),'psutil':psutil.__version__,'cpuCount':psutil.cpu_count(),'physicalMemoryBytes':psutil.virtual_memory().total,'samplesDefinition':'RSS/USS process tree; startup peak includes initialization, idle follows real CRUD for API modes; no QQ/AI children','cases':summaries},indent=2),encoding='utf8')
print(out)
