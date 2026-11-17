import sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path('adapters/astrbot').resolve()))
import model_cache as m
class Purpose(unittest.TestCase):
 def test_owned_purpose_routing_and_default(self):
  provider=NS();lease={'tenantId':'t','botId':'b','config':{'model':'main','summaryModel':'summary','favoriteModel':'vision','auxiliaryModel':'judge'}}
  m.bind_system_contract(provider,object(),lease)
  try:
   for phase,field in [('small_summary','summaryModel'),('big_merge','summaryModel'),('sticker_caption','favoriteModel'),('companion_plan','auxiliaryModel')]:
    with m.auxiliary_scope(provider,phase):
     self.assertEqual(m.auxiliary_model(None,provider),lease['config'][field])
     model=lease['config'].pop(field)
     self.assertIsNone(m.auxiliary_model(None,provider))
     lease['config'][field]=model
     wrong=NS(get_extra=lambda key:dict(lease,botId='other'))
     self.assertIsNone(m.auxiliary_model(wrong,provider))
   with m.auxiliary_scope(provider,'foreground'):self.assertIsNone(m.auxiliary_model(None,provider))
  finally:m.unbind_system_contract(provider)
if __name__=='__main__':unittest.main()
