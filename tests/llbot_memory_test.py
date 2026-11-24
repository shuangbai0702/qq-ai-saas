import importlib.util,unittest,tempfile,json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('runtime_memory',ROOT/'scripts/runtime_memory.py')
memory=importlib.util.module_from_spec(spec);spec.loader.exec_module(memory)
class LLBotMemoryTest(unittest.TestCase):
 def test_busy_qq_descendant_prevents_idle_reclamation(self):
  state={}
  common=dict(created=1,activity=0,busy=False,available=500*memory.MIB,total=4000*memory.MIB,platform='win32')
  self.assertEqual(memory.decision(state,{**common,'at':100,'cpu':0}),'waiting-idle')
  self.assertEqual(memory.decision(state,{**common,'at':170,'cpu':20}),'waiting-idle')
  self.assertEqual(memory.decision(state,{**common,'at':240,'cpu':20}),'reclaim')
 def test_new_child_cpu_counter_reset_defers_instead_of_trimming_busy_tree(self):
  state={'created':1,'at':100,'cpu':50,'lastBusy':0,'nextAllowed':0}
  self.assertEqual(memory.decision(state,dict(created=1,at=200,cpu=2,activity=0,busy=False,available=500*memory.MIB,total=4000*memory.MIB)),'waiting-idle')
 def test_llbot_sweep_samples_descendant_cpu_instead_of_idle_parent_only(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'protocol.process.json').write_text(json.dumps(dict(pid=10,created=1)))
   parent=NS(create_time=lambda:1,cmdline=lambda:['node','adapters/protocol/llbot/runner.mjs'],cpu_times=lambda:(0,0))
   child=NS(create_time=lambda:2,cpu_times=lambda:(12,10))
   samples=[]
   def decide(state,sample):samples.append(sample);return 'active'
   with patch.object(memory,'owned',return_value=parent),patch.object(memory,'owned_tree',return_value=[(parent,1),(child,2)]),patch.object(memory,'activity',return_value=(0,False)),patch.object(memory,'decision',side_effect=decide):
    result=memory.sweep([dict(id='one',pid=10,created=1,root=folder,kind='protocol')])
   self.assertEqual(samples[0]['cpu'],22);self.assertEqual(result[0]['decision'],'active')
if __name__=='__main__':unittest.main()
