"""On-demand rollback. Never restore business databases or login state."""
from pathlib import Path
import subprocess,sys
base=Path(__file__).resolve().parent
sys.path.insert(0,str(Path('.local/mobile-predeploy-20261112').resolve()));from upload import connect
remote='C:/FishBotPredeploy20261112/control/b-release-20261113'
with connect()as c:
 s=c.open_sftp()
 for source,target in [('manage-release.rollback-ready.mjs','manage-release.mjs'),('operator-release.rollback-ready.mjs','operator-release.mjs')]:s.put(str(base/source),remote+'/'+target)
def run(op):
 print('ROLLBACK STEP',op,flush=True);subprocess.run([sys.executable,str(base/'remote.py'),op],check=True)
run('baseline');run('hold-rollback')
try:
 run('restore');run('rollback');run('restart-api');run('resume');run('status');print('ROLLBACK_SUCCEEDED')
except BaseException:
 print('ROLLBACK_INCOMPLETE_KEEP_HELD: inspect exact ownership and file transaction; do not blindly restore business state',flush=True);raise
