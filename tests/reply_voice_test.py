import hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import reply_voice


class ReplyVoice(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.setting={'root':str(self.root),'botId':'bot-a','tenantId':'tenant-a','config':{'systemPrompt':'persona'}}
        self.data=b'approved sound';(self.root/'reply-voice').mkdir();(self.root/'reply-voice/short.mp3').write_bytes(self.data)
        self.catalog={'version':1,'botId':'bot-a','tenantId':'tenant-a','promptSha256':hashlib.sha256(b'persona').hexdigest(),'default':'short','prefix':'！？','suffix':'？！','bodyLengths':[2,3,5],'assets':{'short':{'file':'short.mp3','sha256':hashlib.sha256(self.data).hexdigest()},'long':{'file':'short.mp3','sha256':hashlib.sha256(self.data).hexdigest()}}}
        self.save()
    def save(self):self.root.joinpath('reply-voice.json').write_text(json.dumps(self.catalog),encoding='utf-8')
    def test_tenant_bot_and_persona_are_all_bound(self):
        self.assertIsNotNone(reply_voice.load(self.setting))
        for field,value in [('tenantId','tenant-b'),('botId','bot-b'),('config',{'systemPrompt':'new-persona'})]:
            self.assertIsNone(reply_voice.load({**self.setting,field:value}))
    def test_model_selects_only_an_approved_identity_not_a_path(self):
        c=reply_voice.load(self.setting)
        for selector,choice in [('[VOICE:long]','long'),('[VO\nICE:shor\nt]','short'),('[VOICE:../../other]','short'),('[VOICE:http://bad]','short'),('','short')]:
            self.assertEqual(reply_voice.render(c,'！？奶娃？！\n\n'+selector),('！？奶娃？！',choice))
    def test_old_laughter_and_internal_diagnostics_cannot_escape_envelope(self):
        self.assertEqual(reply_voice.render(reply_voice.load(self.setting),'！？奶娃？！\n\n哈哈哈哈……\n\n工具调用轮数到上限了，请去 WebUI 设置\n\n[VOICE:long]'),('！？奶娃？！','long'))
    def test_invalid_asset_is_not_replaced_or_silently_accepted(self):
        c=reply_voice.load(self.setting);self.assertEqual(reply_voice.materialize(c,'short'),self.data)
        self.root.joinpath('reply-voice/short.mp3').write_bytes(b'changed')
        with self.assertRaises(ValueError):reply_voice.materialize(c,'short')
        self.catalog['assets']['short']['file']='../other.mp3';self.save();self.assertIsNone(reply_voice.load(self.setting))
    def test_missing_catalog_keeps_ordinary_chat_and_removes_internal_marker(self):
        self.assertEqual(reply_voice.render(None,'普通聊天\n[VOICE:long]'),('普通聊天\n',None))
        self.root.joinpath('reply-voice.json').write_text('[]');self.assertIsNone(reply_voice.load(self.setting))


if __name__=='__main__':unittest.main()
