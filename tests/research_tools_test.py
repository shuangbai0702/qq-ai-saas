import json,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from research_tools import video_results,availability
import research_tools


class SearchResults(unittest.TestCase):
    def test_rejected_payload_is_not_an_empty_success(self):
        for value in ({'code':-412},{'code':-101},[],{'code':0,'data':{'result':'wrong'}}):
            with self.assertRaises(ValueError):video_results(value)
    def test_links_are_constructed_from_validated_bvid_not_remote_urls(self):
        rows=video_results({'code':0,'data':{'result':[{'bvid':'../../private','arcurl':'file:///secret'},{'bvid':'BV1234567890','arcurl':'https://evil.example','title':'<em>真实</em>&amp;标题','description':'x'*1000}]}})
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['url'],'https://www.bilibili.com/video/BV1234567890');self.assertEqual(rows[0]['title'],'真实&标题');self.assertEqual(len(rows[0]['description']),240)
    def test_external_context_is_bounded(self):
        self.assertEqual(len(video_results({'code':0,'data':{'result':[{'bvid':'BV1234567890'}]*100}})),5)
    def test_subsecond_cooldown_is_not_treated_as_available(self):
        with patch('research_tools._cooldown_loaded',True),patch('research_tools._cooldown_until',100.4),patch('research_tools.time.monotonic',return_value=100.0):
            self.assertEqual(availability(),{'available':False,'retryAfterSeconds':1})
    def test_cooldown_survives_runtime_restart_and_then_expires(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(research_tools.os.environ,{'ASTRBOT_ROOT':root}),patch.object(research_tools,'_cooldown_loaded',False),patch.object(research_tools,'_cooldown_until',0):
            with patch.object(research_tools.time,'time',return_value=1000),patch.object(research_tools.time,'monotonic',return_value=100):
                research_tools._record_cooldown(900)
            self.assertEqual(json.loads((Path(root)/'bilibili-service-cooldown.json').read_text())['until'],1900)
            research_tools._cooldown_loaded=False;research_tools._cooldown_until=0
            with patch.object(research_tools.time,'time',return_value=1300),patch.object(research_tools.time,'monotonic',return_value=5):
                self.assertEqual(availability(),{'available':False,'retryAfterSeconds':600})
            with patch.object(research_tools.time,'monotonic',return_value=606):
                self.assertEqual(availability(),{'available':True,'retryAfterSeconds':0})
            research_tools._cooldown_loaded=False;research_tools._cooldown_until=0
            with patch.object(research_tools.time,'time',return_value=1901):
                self.assertEqual(availability(),{'available':True,'retryAfterSeconds':0})
    def test_storage_failure_keeps_actual_in_process_cooldown(self):
        with patch.object(research_tools,'_cooldown_loaded',True),patch.object(research_tools,'_cooldown_until',0),patch.object(research_tools,'_cooldown_file',return_value=Path('/missing/state.json')),patch.object(research_tools.tempfile,'mkstemp',side_effect=OSError('unavailable storage')),patch.object(research_tools.time,'monotonic',return_value=50):
            with self.assertLogs('research_tools',level='WARNING'):research_tools._record_cooldown(900)
            self.assertEqual(availability(),{'available':False,'retryAfterSeconds':900})


if __name__=='__main__':unittest.main()
