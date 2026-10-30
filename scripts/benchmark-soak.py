"""Single compiled API+Supervisor idle soak after real registration/20 bot CRUD."""
import argparse,json,os,platform,statistics,subprocess,time
from pathlib import Path
import psutil
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=180);parser.add_argument('--label',default='soak-'+time.strftime('%Y%m%d-%H%M%S'));parser.add_argument('--semi-space-mib',type=int);args=parser.parse_args()
out=root/'experiments/round2-platform/results'/args.label;out.mkdir(parents=True,exist_ok=False)
env={**os.environ,'BENCHMARK_SECONDS':str(args.seconds),'DB_MEMORY_PROFILE':'compact'};env.pop('DATABASE_URL',None)
with(out/'stdout.log').open('w')as stdout,(out/'stderr.log').open('w')as stderr:
    flags=['--max-old-space-size=128']+([f'--max-semi-space-size={args.semi_space_mib}']if args.semi_space_mib else[])
    child=subprocess.Popen(['node',*flags,'experiments/round2-platform/worker.mjs','api-agent',str(out)],cwd=root,env=env,stdout=stdout,stderr=stderr);started=time.monotonic();rows=[]
    while child.poll()is None:
        try:
            p=psutil.Process(child.pid);tree=[p,*p.children(recursive=True)];rows.append({'elapsedSeconds':time.monotonic()-started,'rssBytes':sum(x.memory_info().rss for x in tree),'ussBytes':sum(getattr(x.memory_full_info(),'uss',0)for x in tree),'cpuSeconds':sum(sum(x.cpu_times()[:2])for x in tree),'ready':(out/'ready.json').exists()})
        except(psutil.NoSuchProcess,psutil.AccessDenied):pass
        time.sleep(1)
        if time.monotonic()-started>args.seconds+60:child.terminate();child.wait(10);break
    code=child.wait();idle=[r for r in rows if r['ready']];summary={'platform':platform.platform(),'exitCode':code,'durationSeconds':rows[-1]['elapsedSeconds']if rows else 0,'rssPeakMiB':max((r['rssBytes']for r in rows),default=0)/2**20,'rssFirstIdle10MedianMiB':statistics.median(r['rssBytes']for r in idle[:10])/2**20 if idle else None,'rssLastIdle10MedianMiB':statistics.median(r['rssBytes']for r in idle[-10:])/2**20 if idle else None,'cpuSeconds':rows[-1]['cpuSeconds']if rows else None,'note':'API+embedded Agent with20 stopped bots; not a full QQ workload or long-term leak proof'}
(out/'samples.json').write_text(json.dumps(rows,indent=2));(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
