"""Isolated Job ownership, crash cleanup, authority and restart primitives."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('windows_supervisor',ROOT/'scripts/windows-production-supervisor.py')
supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)


class Authority(unittest.TestCase):
    def test_preflight_never_runs_agent_even_when_authorized(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'authority';file.write_text('target-host\n')
            config=dict(executionAuthority=str(file),authorityId='target-host')
            self.assertTrue(supervisor.allowed(config,False))
            self.assertFalse(supervisor.allowed(config,True))
            file.write_text('source-host')
            self.assertFalse(supervisor.allowed(config,False))
            file.unlink();self.assertFalse(supervisor.allowed(config,False))

    @unittest.skipUnless(sys.platform=='win32','Windows installer planning')
    def test_installer_defaults_to_nonadmin_service_account_without_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private=root/'private';private.mkdir();pgbin=root/'postgres'/'bin';pgbin.mkdir(parents=True)
            python_base=root/'python-base-version';python_base.mkdir();junction=root/'python-base'
            linked=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(junction),str(python_base)],capture_output=True,timeout=10)
            self.assertEqual(linked.returncode,0)
            config=dict(sourceRoot=str(ROOT),dataRoot=str(private/'runtime'),stateRoot=str(private/'state'),postgresData=str(private/'postgres'),node=sys.executable,python=sys.executable,postgresBin=str(pgbin),executionAuthority=str(private/'authority'),readOnlyRoots=[str(junction)],environment={'FISHBOT_MODEL_KEY':'never-print-this-secret'})
            file=root/'config.json';file.write_text(json.dumps(config))
            args=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/install-windows-production-task.ps1'),'-Config',str(file),'-Preflight','-PlanOnly']
            result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('never-print-this-secret',result.stdout)
            plan=json.loads(result.stdout)
            self.assertEqual((plan['sid'],plan['runLevel']),('S-1-5-20','Limited'))
            self.assertEqual(len(plan['modifyDirectories']),3)
            self.assertIn(str(python_base),plan['readDirectories'])
            self.assertNotIn(str(junction),plan['readDirectories'])
            result=subprocess.run([*args,'-ServiceAccount','LocalService'],capture_output=True,text=True,encoding='utf-8',timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['sid'],'S-1-5-19')
            result=subprocess.run([*args,'-ServiceAccount','SYSTEM'],capture_output=True,text=True,encoding='utf-8',timeout=15)
            self.assertNotEqual(result.returncode,0)


@unittest.skipUnless(sys.platform=='win32','Windows Job Objects')
class NativeOwnership(unittest.TestCase):
    def wait_for(self,predicate,seconds=10):
        end=time.monotonic()+seconds
        while not predicate():
            if time.monotonic()>end:self.fail('owned process deadline exceeded')
            time.sleep(.025)

    def test_actual_acl_grant_reaches_protected_existing_files_and_future_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);script=root/'acl-check.ps1'
            script.write_text(r'''param([string]$Helper,[string]$Root)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1')
. $Helper
$sid=[Security.Principal.SecurityIdentifier]::new('S-1-5-20')
$current=[Security.Principal.WindowsIdentity]::GetCurrent().User
$result=@()
foreach($permission in @('RX','M')) {
 $directory=Join-Path $Root $permission
 New-Item -ItemType Directory -Path $directory | Out-Null
 $file=Join-Path $directory 'protected.dll'
 Set-Content -LiteralPath $file -Value 'isolated fixture'
 $acl=Get-Acl -LiteralPath $file
 $acl.SetAccessRuleProtection($true,$false)
 $acl.PurgeAccessRules($sid)
 $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($current,'FullControl','Allow'))
 Set-Acl -LiteralPath $file -AclObject $acl
 Grant-FishBotServiceAccess -Path $directory -Sid $sid.Value -Permission $permission -Directory $true
 $after=Get-Acl -LiteralPath $file
 $desired=if($permission -eq 'RX'){[Security.AccessControl.FileSystemRights]::ReadAndExecute}else{[Security.AccessControl.FileSystemRights]::Modify}
 $rules=@($after.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]) | Where-Object {$_.IdentityReference.Value -eq $sid.Value -and (($_.FileSystemRights -band $desired) -eq $desired) -and $_.InheritanceFlags -eq 'None'})
 if(-not $after.AreAccessRulesProtected -or $rules.Count -eq 0){throw 'Protected file lacks the required explicit service ACE'}
 $future=Join-Path $directory 'future.dll'
 Set-Content -LiteralPath $future -Value 'future fixture'
 $inherited=@((Get-Acl -LiteralPath $future).GetAccessRules($false,$true,[Security.Principal.SecurityIdentifier]) | Where-Object {$_.IdentityReference.Value -eq $sid.Value -and (($_.FileSystemRights -band $desired) -eq $desired)})
 if($inherited.Count -eq 0){throw 'New child lacks inherited service access'}
 $result += [pscustomobject]@{permission=$permission;protected=$true;explicit=$true;futureInherited=$true}
}
$result | ConvertTo-Json
''',encoding='utf-8')
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Helper',str(ROOT/'scripts/windows-service-acl.ps1'),'-Root',str(root)],capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            rows=json.loads(result.stdout)
            self.assertEqual([row['permission'] for row in rows],['RX','M'])

    def test_real_exit_reaps_descendant_before_restart_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);worker=root/'worker.py';descendant=root/'descendant.json'
            worker.write_text("import subprocess,sys,time,json,psutil\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath(sys.argv[1]).write_text(json.dumps({'pid':p.pid,'created':psutil.Process(p.pid).create_time()}))\nprint('private-credential-fixture',flush=True)\ntime.sleep(.5)\n")
            service=supervisor.Service('agent',[sys.executable,str(worker),str(descendant)],root,{**os.environ,'FISHBOT_MODEL_KEY':'private-credential-fixture'},root,root,10)
            try:
                service.start();first=service.process
                self.wait_for(descendant.exists)
                child=json.loads(descendant.read_text());process=psutil.Process(child['pid'])
                self.wait_for(lambda:first.poll() is not None)
                self.assertTrue(service.reap(100))
                self.wait_for(lambda:not process.is_running())
                self.assertEqual(service.next,110)
                record=json.loads(service.record().read_text());self.assertEqual(record['pid'],first.pid)
                log=(root/'logs/agent.log').read_text()
                self.assertNotIn('private-credential-fixture',log);self.assertIn('[REDACTED]',log)
                service.start();self.assertNotEqual(service.process.pid,first.pid)
                self.assertEqual(json.loads(service.record().read_text())['pid'],service.process.pid)
            finally:service.stop()

    def test_abrupt_supervisor_death_closes_job_and_kills_full_nested_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);worker=root/'worker.py';info=root/'tree.json';helper=root/'helper.py'
            worker.write_text("import subprocess,sys,time,json,os,psutil\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath(sys.argv[1]).write_text(json.dumps([{'pid':os.getpid(),'created':psutil.Process().create_time()},{'pid':p.pid,'created':psutil.Process(p.pid).create_time()}]))\ntime.sleep(60)\n")
            helper.write_text("import sys,time,importlib.util\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location('supervisor',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nj=m.WindowsJob();j.assign()\nr=Path(sys.argv[2]);service=m.Service('agent',[sys.executable,str(r/'worker.py'),str(r/'tree.json')],r,dict(__import__('os').environ),r,r);service.start()\ntime.sleep(60)\n")
            parent=subprocess.Popen([sys.executable,str(helper),str(ROOT/'scripts/windows-production-supervisor.py'),str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                self.wait_for(info.exists)
                rows=json.loads(info.read_text());processes=[psutil.Process(row['pid']) for row in rows]
                parent.terminate();parent.wait(5)
                self.wait_for(lambda:all(not p.is_running() for p in processes))
            finally:
                if parent.poll() is None:parent.kill();parent.wait(5)
                parent.stderr.close()


if __name__=='__main__':unittest.main()
