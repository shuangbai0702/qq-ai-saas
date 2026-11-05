"""Bounded syscall and cancellation checks, without advising real processes."""
import importlib.util
from pathlib import Path
import os,tempfile,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('pageout',Path(__file__).resolve().parents[1]/'scripts/linux-pageout.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class Call:
    def __init__(self,callback):self.callback=callback
    def __call__(self,*args):return self.callback(*args)
class Lib:pass

class PageoutTest(unittest.TestCase):
    def run_case(self,cursor=0,cancel=False,migrate=False):
        with tempfile.TemporaryDirectory() as root:
            control=Path(root);(control/'guard-heartbeat').touch()
            group='/system.slice/docker-abc.scope/fishbot-napcat/runtime-42-100'
            realpath=Path;read=Path.read_text;calls=[];closed=[];realclose=os.close
            def path(value):
                text=str(value)
                if text=='/var/lib/fishbot-memory-control':return control
                if text.startswith('/var/lib/fishbot-memory-control/'):
                    return control/text.split('/var/lib/fishbot-memory-control/',1)[1]
                return realpath(value)
            def read_text(p,*a,**kw):
                if p.as_posix()=='/proc/42/cgroup':return '0::'+(group if not migrate or not calls else '/other')+'\n'
                return read(p,*a,**kw)
            def advise(fd,vec,n,mode,flags):
                calls.append((vec._obj.base,vec._obj.length))
                if cancel:(control/('activity-wake-runtime-42-100')).touch()
                return vec._obj.length
            lib=Lib();lib.process_madvise=Call(advise)
            with patch.object(mod,'Path',side_effect=path),patch.object(Path,'read_text',read_text),patch.object(mod,'start_time',return_value='100'),patch.object(mod,'ranges',return_value=iter([(0x1000000,0x11000000)])),patch.object(mod.ctypes,'CDLL',return_value=lib),patch.object(mod.os,'pidfd_open',return_value=123,create=True),patch.object(mod.os,'close',side_effect=lambda fd:closed.append(fd) if fd==123 else realclose(fd)):
                if migrate:
                    with self.assertRaisesRegex(RuntimeError,'ownership'):mod.pageout(42,'100',group,cursor)
                    result=None
                else:result=mod.pageout(42,'100',group,cursor)
            self.assertEqual(closed,[123])
            return result,calls
    def test_budget_and_cursor_continue_large_mapping(self):
        result,calls=self.run_case()
        self.assertEqual(result['advisedVirtualBytes'],64*1048576)
        self.assertEqual(len(calls),16)
        self.assertTrue(all(size<=4*1048576 for _,size in calls))
        nxt,more=self.run_case(result['nextCursor'])
        self.assertEqual(more[0][0],result['nextCursor'])
        self.assertGreater(nxt['nextCursor'],result['nextCursor'])
    def test_activity_between_chunks_stops_further_syscalls(self):
        result,calls=self.run_case(cancel=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['advisedVirtualBytes'],4*1048576)
    def test_migration_between_chunks_fails_closed_and_closes_fd(self):
        self.run_case(migrate=True)

if __name__=='__main__':unittest.main()

