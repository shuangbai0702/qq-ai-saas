"""Explicit production gate; preparation alone never runs this driver."""
import sys,subprocess,json,hashlib
from pathlib import Path
p=Path(__file__).resolve().parent
gate=json.loads((p/'deployment-gate.json').read_text())
assert gate.get('releaseAuthorized') is True and gate.get('regressionClosed') is True and gate.get('mediaClosed') is True
assert gate['manifestSha256']==hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest(),'Gate does not authorize these exact bytes'
def run(op):
 print('STEP',op,flush=True);subprocess.run([sys.executable,str(p/'remote.py'),op],check=True)
held=False;install_attempted=False;api_restarted=False;backup_attempted=False;backup_completed=False
try:
 held=True;run('hold');run('check');run('backup-ready');backup_attempted=True;run('backup');backup_completed=True;install_attempted=True;run('install')
 for op in ['hotmaintenance','reload','attest','restart-api','verify']:
  if op=='restart-api':api_restarted=True
  run(op)
 run('resume');held=False;run('status');print('DEPLOY_SUCCEEDED',flush=True)
except BaseException:
 if held:
  print('DEPLOY_FAILURE_ROLLBACK',flush=True)
  try:
   if backup_attempted and not backup_completed:run('backup-stopped')
   if install_attempted:run('restore')
   run('rollback')
   if api_restarted:run('restart-api')
   run('resume');run('status')
  except BaseException as e:print('ROLLBACK_NEEDS_OPERATOR',type(e).__name__,flush=True)
 raise
