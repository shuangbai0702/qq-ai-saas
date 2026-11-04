#!/usr/bin/env python3
"""Host-root installer: delegate only a new media subtree, preserve QQ owners.
No container restart, Docker socket mount, global cgroup remount or hard RAM cap.
Run on host with --container NAME --data-root HOST_RUNTIME_ROOT [--apply].
"""
import argparse,ctypes,json,os,subprocess,sys,platform
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--container',required=True);p.add_argument('--data-root',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
if os.geteuid()!=0:raise SystemExit('host root required')
s=json.loads(subprocess.check_output(['docker','inspect',a.container],text=True))[0];pid=s['State']['Pid']
if not s['State']['Running'] or not pid:raise SystemExit('container not running')
relative=next(v.split(':',2)[2] for v in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if v.startswith('0::'))
parent=Path('/sys/fs/cgroup'+relative)
if parent.name.startswith('fishbot-'):parent=parent.parent
if not parent.name.startswith('docker-') or not parent.name.endswith('.scope'):raise SystemExit('unrecognized owner cgroup')
guard=Path('/var/lib/fishbot-memory-control');root=Path(a.data_root).resolve()
if not any(Path(m['Source']).resolve()==root and m['Destination']=='/var/lib/fishbot' for m in s['Mounts']):raise SystemExit('runtime volume owner mismatch')
if not (guard/'armed').exists() or (guard/'disabled').exists():raise SystemExit('Guard not armed')
import time
if time.time()-(guard/'guard-heartbeat').stat().st_mtime>10:raise SystemExit('Guard heartbeat stale')
group=parent/'fishbot-media'
if not a.apply:print(json.dumps({'readyToDelegate':True,'containerPid':pid,'group':str(group),'runtime':str(root)}));raise SystemExit(0)
if (parent/'cgroup.procs').read_text().strip():raise SystemExit('domain parent has direct processes; refuse migration')
io_accounting='io' in (parent/'cgroup.controllers').read_text().split()
(parent/'cgroup.subtree_control').write_text('+memory +cpu +pids'+(' +io' if io_accounting else ''))
group.mkdir(exist_ok=True)
if (group/'cgroup.procs').read_text().strip():raise SystemExit('media parent unexpectedly contains processes')
(group/'memory.high').write_text(str(192*1048576));(group/'memory.max').write_text('max');(group/'cpu.max').write_text('20000 100000');(group/'pids.max').write_text('64');(group/'cgroup.subtree_control').write_text('+memory +cpu +pids'+(' +io' if io_accounting else ''))
# Keep source dir FDs across setns/chroot. fchdir avoids depending on whether
# the container's procfs exposes the host installer's PID/FD directory.
fd_group=os.open(group,os.O_RDONLY|os.O_DIRECTORY);fd_guard=os.open(guard,os.O_RDONLY|os.O_DIRECTORY);fd_root=os.open(f'/proc/{pid}/root',os.O_RDONLY|os.O_DIRECTORY);fd_ns=os.open(f'/proc/{pid}/ns/mnt',os.O_RDONLY)
lib=ctypes.CDLL(None,use_errno=True)
def checked(result,what):
 if result!=0:raise OSError(ctypes.get_errno(),what)
# Detached mount clones can move across namespaces without exposing the
# host root/procfs inside the container. Linux 5.2+ mount API.
if platform.machine() not in ('x86_64','aarch64'):raise SystemExit('unsupported syscall architecture')
lib.syscall.restype=ctypes.c_long
mounts=[]
for source,target,readonly in [(group,'/run/fishbot-media-cgroup',False),(guard,'/run/fishbot-media-guard',True)]:
 fd=lib.syscall(428,-100,str(source).encode(),1|os.O_CLOEXEC)
 if fd<0:raise OSError(ctypes.get_errno(),'open_tree media resource')
 mounts.append((fd,target,readonly))
# Child namespace changes never affect the installer's host namespace.
child=os.fork()
if child==0:
 try:
  checked(lib.setns(fd_ns,0x00020000),'setns');os.fchdir(fd_root);os.chroot('.');os.chdir('/')
  for fd,target,readonly in mounts:
   Path(target).mkdir(exist_ok=True)
   if os.path.ismount(target):
    current=os.stat(target);source=os.fstat(fd)
    if (current.st_dev,current.st_ino)!=(source.st_dev,source.st_ino):raise RuntimeError('target already belongs to another mount')
    checked(lib.mount(None,target.encode(),None,4096|32|(1 if readonly else 0),None),'verify resource remount flags')
    continue
   checked(lib.syscall(429,fd,b'',-100,target.encode(),4),'move_mount dedicated media resource')
   checked(lib.mount(None,target.encode(),None,4096|32|(1 if readonly else 0),None),'remount dedicated resource')
  os._exit(0)
 except BaseException as error:
  print(repr(error),file=sys.stderr,flush=True);os._exit(1)
_,status=os.waitpid(child,0)
for fd in [fd_group,fd_guard,fd_root,fd_ns]+[m[0] for m in mounts]:os.close(fd)
if status:raise SystemExit('namespace media mounts failed; QQ untouched, media remains unavailable')
config={'cgroupRoot':'/run/fishbot-media-cgroup','guardRoot':'/run/fishbot-media-guard','ffmpeg':'/usr/bin/ffmpeg','ffprobe':'/usr/bin/ffprobe'}
file=root/'media-resources.json'
if file.exists():
 old=json.loads(file.read_text())
 if old!=config:raise SystemExit('existing media config differs; not overwriting')
else:
 fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as out:json.dump(config,out)
print(json.dumps({'delegated':True,'containerPid':pid,'group':str(group),'memoryHighMiB':192,'memoryMax':'max','cpuMax':'20000 100000','ioAccounting':io_accounting,'qqRestarted':False}))
