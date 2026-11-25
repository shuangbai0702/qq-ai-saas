"""Detached QQ ownership and real collector wiring; never operates on live QQ."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import llbot_processes as native
import runtime_memory as memory
import memory_snapshot as snapshot
import process_control as control


class Process:
    def __init__(self, pid, born=100, args=(), exe=''):
        self.pid, self.born, self.args, self.executable = pid, born, list(args), str(exe)
        self.descendants = []
    def create_time(self): return self.born
    def cmdline(self): return self.args
    def exe(self): return self.executable
    def children(self): return self.descendants
    def is_running(self): return True
    def memory_info(self): return NS(rss=4096, vms=8192)
    def memory_full_info(self): return NS(uss=1024)
    def ppid(self): return 1
    def name(self): return 'fixture.exe'
    def cpu_times(self): return NS(user=0, system=0)
    def num_threads(self): return 1
    def io_counters(self): return NS(read_bytes=0, write_bytes=0)


class LLBotNativeOwnership(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name); self.root = self.data/'bots'/'bot-a'
        home = self.root/'llbot'/'qq'; home.mkdir(parents=True)
        self.profile, self.file = home/'profile', home/'profile-proof.json'
        self.manifest = self.root/'llbot-platform.json'
        self.manifest.write_text(json.dumps(dict(root=str(self.root),botId='bot-a',generation=2)))
        # AI generation may have changed without restarting the QQ owner.
        self.proof = dict(botId='bot-a',generation=1,pid=20,profile=str(self.profile),network=True)
        self.write_proof()
        self.owner = Process(10,args=['node','adapters/protocol/llbot/runner.mjs',str(self.manifest)])
        self.qq = Process(20,born=102,args=['QQ.exe','--user-data-dir='+str(self.profile)],exe=home/'program'/'QQ.exe')
        self.helper, self.renderer = Process(11), Process(21,born=103)
        self.owner.descendants = [self.helper]; self.qq.descendants = [self.renderer]
        self.processes = {p.pid:p for p in [self.owner,self.qq,self.helper,self.renderer]}
        self.row = dict(pid=10,created=100,root=str(self.root))
        (self.root/'protocol.process.json').write_text(json.dumps(self.row))
        patcher = patch.object(native.psutil,'Process',side_effect=self.lookup)
        patcher.start(); self.addCleanup(patcher.stop)
        platform = patch.object(memory.sys,'platform','win32'); platform.start(); self.addCleanup(platform.stop)

    def lookup(self, pid):
        if pid not in self.processes: raise native.psutil.NoSuchProcess(pid)
        return self.processes[pid]

    def write_proof(self):
        self.file.write_text(json.dumps(self.proof)); os.utime(self.file,(105,105))

    def test_detached_qq_is_included_once_in_both_collectors(self):
        self.helper.descendants = [self.qq]  # Also reachable through parentage.
        self.assertEqual({p.pid for p,_ in memory.owned_tree(self.row)}, {10,11,20,21})
        self.assertEqual(len(memory.owned_tree(self.row)),4)
        self.processes[1] = Process(1)
        with patch.object(snapshot,'windows_compression',return_value={}):
            result = snapshot.collect(dict(root=str(self.data),apiPid=1))
        self.assertEqual(result['errors'],[])
        self.assertEqual({p['pid'] for p in result['processes']},{1,10,11,20,21})
        inspected = control.inspect(10,str(self.root),100)
        self.assertTrue(inspected['valid']); self.assertEqual(inspected['processCount'],4)

    def test_rejects_other_bot_profile_executable_or_proof(self):
        cases = [dict(botId='bot-b'),dict(profile=str(self.profile)+'-other'),dict(network=False),dict(pid=True)]
        for change in cases:
            with self.subTest(change=change):
                before = self.proof.copy(); self.proof.update(change); self.write_proof()
                with self.assertRaises(ValueError): memory.owned_tree(self.row)
                self.proof = before
        self.write_proof()
        self.qq.executable = str(self.root.parent/'bot-b'/'llbot'/'qq'/'program'/'QQ.exe')
        with self.assertRaisesRegex(ValueError,'owner-mismatch'): memory.owned_tree(self.row)
        self.qq.executable = str(self.root/'llbot'/'qq'/'program'/'QQ.exe')
        self.qq.args = ['QQ.exe','--user-data-dir='+str(self.profile)+'-other']
        with self.assertRaisesRegex(ValueError,'owner-mismatch'): memory.owned_tree(self.row)

    def test_reused_pid_and_previous_owner_qq_are_rejected(self):
        for birth in [106,99]:
            with self.subTest(birth=birth):
                self.qq.born = birth
                with self.assertRaisesRegex(ValueError,'lifetime-mismatch'): memory.owned_tree(self.row)

    def test_proof_change_during_discovery_never_publishes_old_native_tree(self):
        def changed():
            self.proof['pid'] = 30; self.write_proof(); return []
        self.qq.descendants = []; self.qq.children = changed
        self.processes[30] = Process(30,born=104,args=self.qq.args,exe=self.qq.executable)
        with self.assertRaisesRegex(ValueError,'lifetime-mismatch'): memory.owned_tree(self.row)

    def test_missing_native_proof_is_an_error_not_an_eight_process_success(self):
        self.file.unlink(); self.processes[1] = Process(1)
        result = snapshot.collect(dict(root=str(self.data),apiPid=1))
        self.assertEqual([p['pid'] for p in result['processes']],[1])
        self.assertEqual(result['errors'][0]['reason'],'native-ownership-unavailable')
        inspected=control.inspect(10,str(self.root),100)
        self.assertTrue(inspected['valid'],'missing native proof during login is not a dead protocol owner')
        self.assertIsNone(inspected['rssBytes'])
        self.assertEqual(inspected['memoryCoverage'],'native-ownership-unavailable')

    def test_non_llbot_does_not_read_or_adopt_native_proof(self):
        self.owner.args = ['node',str(self.root/'napcat-config.json')]
        self.assertEqual({p.pid for p,_ in memory.owned_tree(self.row)},{10,11})

    def test_reclaim_reaches_detached_qq_descendants_and_refuses_reused_pid(self):
        kernel, api = MagicMock(), MagicMock()
        kernel.OpenProcess.side_effect = lambda rights, inherit, pid: pid
        api.EmptyWorkingSet.return_value = True
        with patch.object(memory.ctypes,'WinDLL',side_effect=lambda name,**kw:kernel if name=='kernel32'else api):
            result = memory.reclaim(self.row,'win32')
            self.assertEqual(result['processesReclaimed'],4)
            self.assertEqual({c.args[0]for c in api.EmptyWorkingSet.call_args_list},{10,11,20,21})
            api.EmptyWorkingSet.reset_mock(); self.qq.born = 106
            with self.assertRaises(ValueError): memory.reclaim(self.row,'win32')
            api.EmptyWorkingSet.assert_not_called()


if __name__ == '__main__': unittest.main()
