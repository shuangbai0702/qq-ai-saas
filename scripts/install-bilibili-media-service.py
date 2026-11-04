#!/usr/bin/env python3
"""Install an idempotent host-only delegation repair, without restarting QQ.

The timer runs a short oneshot, not a resident worker. Namespace mounts disappear
when the Agent container is recreated; the existing Guard must be armed before
the delegation script can restore them. Existing unrelated units are refused.
"""
import argparse,json,os,subprocess
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--container',required=True)
p.add_argument('--data-root',required=True)
p.add_argument('--source-root',required=True)
p.add_argument('--apply',action='store_true')
a=p.parse_args()
if os.geteuid()!=0:raise SystemExit('host root required')
script=Path(a.source_root).resolve()/'scripts/delegate-bilibili-media.py'
if not script.is_file():raise SystemExit('delegation script missing')
if any('\n' in v or '\r' in v or '%' in v for v in [str(script),a.container,a.data_root]):raise SystemExit('invalid service argument')
args=['/usr/bin/python3',str(script),'--container',a.container,'--data-root',str(Path(a.data_root).resolve()),'--apply']
service='''# FishBot owned Bilibili media delegation v1
[Unit]
Description=Restore dedicated FishBot media resources after container recreation
After=docker.service fishbot-memory-guard.service
ConditionPathExists=/run/docker.sock
ConditionPathExists=/var/lib/fishbot-memory-control/armed
ConditionPathExists=!/var/lib/fishbot-memory-control/disabled

[Service]
Type=oneshot
TimeoutStartSec=20
ExecStart='''+' '.join(json.dumps(v) for v in args)+'''\n'''
timer='''# FishBot owned Bilibili media delegation v1
[Unit]
Description=Check FishBot media delegation without a resident worker

[Timer]
OnBootSec=30s
OnUnitInactiveSec=60s
AccuracySec=10s
Unit=fishbot-bilibili-media-delegation.service

[Install]
WantedBy=timers.target
'''
files={Path('/etc/systemd/system/fishbot-bilibili-media-delegation.service'):service,Path('/etc/systemd/system/fishbot-bilibili-media-delegation.timer'):timer}
for file,text in files.items():
 legacy=text.replace('ConditionPathExists=/var/lib/fishbot-memory-control/armed\nConditionPathExists=!/var/lib/fishbot-memory-control/disabled\n','')
 if file.exists() and file.read_text() not in (text,legacy):raise SystemExit('existing unit differs; refuse overwrite: '+str(file))
if not a.apply:print(json.dumps({'readyToInstall':True,'units':[str(f) for f in files]}));raise SystemExit(0)
for file,text in files.items():
 if not file.exists():
  with file.open('x') as out:out.write(text)
 elif file.read_text()!=text:file.write_text(text)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','fishbot-bilibili-media-delegation.timer'],check=True)
subprocess.run(['systemctl','start','fishbot-bilibili-media-delegation.service'],check=True)
print(json.dumps({'installed':True,'residentWorker':False,'intervalSeconds':60,'qqRestarted':False}))
