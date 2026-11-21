import importlib.util,json,subprocess,sys,tempfile,time,unittest
from pathlib import Path
import psutil
spec=importlib.util.spec_from_file_location('process_control',Path(__file__).resolve().parents[1]/'scripts/process_control.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ProcessInstances(unittest.TestCase):
 def test_real_descendants_and_wrong_parent_birth(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);output=root/'child.json';script=root/'launcher.py'
   script.write_text("import subprocess,sys,json,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',sys.argv[1]])\nPath(sys.argv[2]).write_text(json.dumps({'pid':p.pid}))\ntime.sleep(30)\n")
   p=subprocess.Popen([sys.executable,str(script),str(root),str(output)])
   try:
    deadline=time.monotonic()+10
    while not output.exists():
     if time.monotonic()>deadline:self.fail('child startup timeout')
     time.sleep(.02)
    child=json.loads(output.read_text())['pid'];created=psutil.Process(p.pid).create_time();tree=m.inspect(p.pid,str(root),created)
    self.assertTrue(tree['valid']);instances={r['pid']:r['created'] for r in tree['processInstances']}
    self.assertEqual(instances[child],psutil.Process(child).create_time())
    self.assertEqual(instances[p.pid],created)
    self.assertFalse(m.inspect(p.pid,str(root),created-10)['valid'])
   finally:
    m.stop_tree(psutil.Process(p.pid));p.wait(10)
if __name__=='__main__':unittest.main()
