import sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path('adapters/astrbot').resolve()),str(Path(__file__).resolve().parent)]
from message_layout import transport_parts
from qq_diagnostics import failure_notice
class Reply(unittest.TestCase):
 def test_lossless(self):
  for text in ['红棕'+('色😀\n'*1500),'```python\n'+('print(1)\n'*800)+'```','a'*792,'a'*500]:
   parts=transport_parts(text);self.assertEqual(''.join(parts),text);self.assertTrue(all(len(t.encode('utf-16-le'))//2<=1500 for t in parts))
 def test_public_diagnostics(self):
  text=failure_notice('TimeoutError',reference='private-secret-path-token',partial=True)
  self.assertIn('超时',text);self.assertIn('不会自动重发',text);self.assertNotIn('private-secret',text)
  self.assertNotIn('TimeoutError',text)
# Run the installed SDK send wrapper and native Journal fixture, no QQ/network.
from public_output_guard_test import Regeneration
from astrbot.core.message.components import Plain
class SendCompleteness(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=Regeneration.asyncSetUp
 asyncTearDown=Regeneration.asyncTearDown
 activate=Regeneration.activate
 activation_request=Regeneration.activation_request
 scenario=Regeneration.scenario
 async def test_actual_send_preserves_tail_and_confirms_each_chunk(self):
  text='第一段。'+('完整回复内容。'*550)+'结束证据。'
  self.settings['qq']['config']['behavior'].update(splitMessages=False,typingSimulation=False,typoEnabled=False,sendIntervalMs=0)
  event,requests=await self.scenario([text])
  sent=[''.join(x.text for x in message.chain if isinstance(x,Plain)) for message in self.sent]
  self.assertEqual(''.join(sent),text)
  self.assertGreater(len(sent),1)
  self.assertTrue(all(len(x.encode('utf-16-le'))//2<=1500 for x in sent))
  work=event.get_extra('_hosted_pipeline')
  self.assertEqual(work.confirmed_messages,len(sent))
  self.assertEqual(work.confirmed_reply_parts,sent)
  await self.assert_failed_second_send_does_not_repeat_confirmed_prefix()
 async def assert_failed_second_send_does_not_repeat_confirmed_prefix(self):
  from unittest.mock import patch
  self.settings['qq']['config']['behavior'].update(splitMessages=False,typingSimulation=False,typoEnabled=False,sendIntervalMs=0)
  original=self.st.pipeline.confirmed
  calls=0
  def confirm(work):
   nonlocal calls
   calls+=1
   if calls==2:raise TimeoutError('synthetic uncertain receipt')
   return original(work)
  with patch.object(self.st.pipeline,'confirmed',side_effect=confirm):
   with self.assertRaises(TimeoutError):await self.scenario(['文'*3859])
  self.assertEqual(len(self.sent),2)
  # Only the first bubble acquired a receipt; the unknown suffix stops here.
  self.assertEqual(calls,2)
if __name__=='__main__':unittest.main()
