import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import process_control as control
class InspectionDenied(unittest.TestCase):
 def test_denied_is_not_a_dead_process_and_batch_can_continue(self):
  with patch.object(control,'owned_process',side_effect=[control.psutil.AccessDenied(1),None]):
   self.assertEqual(control.inspect(1,'fixture'),{'valid':False,'inspectionError':'access_denied'})
   self.assertEqual(control.inspect(2,'fixture'),{'valid':False})
 def test_stop_ownership_still_refuses_unreadable_command_line(self):
  with patch.object(control.psutil,'Process') as process:
   process.return_value.create_time.return_value=1
   process.return_value.cmdline.side_effect=control.psutil.AccessDenied(1)
   with self.assertRaises(control.psutil.AccessDenied):control.owned_process(1,'fixture',1)
if __name__=='__main__':unittest.main()
