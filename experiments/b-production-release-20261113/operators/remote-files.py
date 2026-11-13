from pathlib import Path
import sys,base64
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,str(Path('.local/mobile-predeploy-20261112').resolve()))
from upload import connect
base=Path('.local/b-release-20261113');remote='C:/FishBotPredeploy20261112/control/b-release-20261113'
with connect() as c:
 s=c.open_sftp()
 try:s.mkdir(remote)
 except OSError:pass
 if sys.argv[1]=='put':
  for name in sys.argv[2:]:
   assert Path(name).name==name
   s.put(str(base/name),remote+'/'+name)
  print('staged',len(sys.argv)-2)
 elif sys.argv[1]=='get':
  for name in sys.argv[2:]:
   assert Path(name).name==name
   s.get(remote+'/'+name,str(base/name))
  print('retrieved',len(sys.argv)-2)
 elif sys.argv[1]=='node':
  name=sys.argv[2];assert Path(name).name==name and name.endswith('.mjs')
  command='node.exe '+remote+'/'+name
  i,o,e=c.exec_command(command,timeout=450)
  output=o.read();error=e.read();status=o.channel.recv_exit_status()
  (base/(name+'.stdout')).write_bytes(output);(base/(name+'.stderr')).write_bytes(error)
  print(output.decode('utf-8',errors='replace'));print(error.decode('utf-8',errors='replace'));raise SystemExit(status)
