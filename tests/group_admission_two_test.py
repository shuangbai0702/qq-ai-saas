import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from shared_context import group_whitelist,included
from native_ingress import admitted
from autonomy import group_allowed

class TwoGroups(unittest.TestCase):
 def test_existing_config_and_legacy_four_group_limit_cannot_activate_extra_groups(self):
  cfg={'behavior':{'enabled':True,'groupParticipationEnabled':True,'groupParticipationGroups':['11111','11111','22222','33333','44444']}}
  self.assertEqual(group_whitelist(cfg,4),('11111','22222'))
  for group in ['11111','22222','33333','44444']:
   expected=group in ['11111','22222']
   self.assertEqual(included('group:'+group,cfg,4),expected)
   self.assertEqual(admitted({'group_id':group},cfg),expected)
   self.assertEqual(group_allowed(cfg,'group:'+group),expected)
  self.assertTrue(admitted({'message_type':'private'},cfg))
 def test_service_scope_still_intersects_the_active_whitelist(self):
  cfg={'groups':['22222'],'behavior':{'groupParticipationEnabled':True,'groupParticipationGroups':['11111','22222','33333']}}
  self.assertFalse(admitted({'group_id':'11111'},cfg))
  self.assertTrue(admitted({'group_id':'22222'},cfg))
  self.assertFalse(group_allowed(cfg,'group:11111'))
  self.assertTrue(group_allowed(cfg,'group:22222'))
if __name__=='__main__':unittest.main()
