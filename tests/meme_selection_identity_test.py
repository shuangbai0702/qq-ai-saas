import json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from social import SocialStore
from context_reading import encode_readonly
import periodic_guidance
from qq_favorites import catalog


class SelectionIdentity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=sqlite3.connect(':memory:')
        self.store=SocialStore(self.db,self.temp.name)
        self.row={'id':'bf_0123456789abcdef','meaning':'开心','avoid':'严肃困难','source':'platform_pack'}
        packet=encode_readonly({}, {'social':{'meme_candidates':[self.row]}},[['set',[],{'social':{'meme_candidates':[self.row]}}]],{})
        self.key=packet['readonlyChanges'][0][2]['social']['meme_candidates']['active_order'][0]
        self.cfg={'memeEnabled':True}

    def tearDown(self):
        self.store.close();self.db.close();self.temp.cleanup()

    def select(self,key,offered=None):
        with patch.object(self.store,'catalog',return_value=[self.row]):
            return self.store.select_image('[MEME:'+key+']','private:1',self.cfg,candidates=[self.row] if offered is None else offered)[1]

    def test_current_version_key_and_original_id_select_same_image(self):
        self.assertEqual(self.select(self.key),self.row)
        self.assertEqual(self.select(self.row['id']),self.row)

    def test_other_version_same_id_and_unoffered_are_rejected(self):
        self.assertIsNone(self.select(self.key,[{**self.row,'meaning':'生气'}]))
        self.assertIsNone(self.select(self.key,[]))
        self.assertIsNone(self.select('v_'+'0'*24))
        with patch.object(self.store,'catalog',return_value=[self.row]):
            self.assertIsNone(self.store.select_image('[MEME:'+self.key+']','private:1',self.cfg)[1])

    def test_removed_catalog_item_and_sensitive_context_still_reject(self):
        with patch.object(self.store,'catalog',return_value=[]):
            self.assertIsNone(self.store.select_image('[MEME:'+self.key+']','private:1',self.cfg,candidates=[self.row])[1])
        self.assertIsNone(self.store.select_image('[MEME:'+self.key+']','private:1',self.cfg,candidates=[self.row],request_text='家人病危')[1])

    def test_favorites_default_on_but_item_and_account_fences_remain(self):
        folder=Path(self.temp.name)/'qq-favorites';(folder/'images').mkdir(parents=True)
        item={'id':'qqf_0123456789abcdef','file':'qqf_0123456789abcdef.gif','enabled':True,'meaning':'开心','manual':True}
        (folder/'images'/item['file']).write_bytes(b'fixture')
        index={'account':'90000','items':[item]}
        def save(): (folder/'index.json').write_text(json.dumps(index))
        save();self.assertEqual(len(catalog(self.temp.name,'90000')),1)
        self.assertEqual(catalog(self.temp.name,'80000'),[])
        index['enabled']=False;save();self.assertEqual(catalog(self.temp.name,'90000'),[])
        index['enabled']=True;item['enabled']=False;save();self.assertEqual(catalog(self.temp.name,'90000'),[])
        item['enabled']=True;item['manual']=False;save();self.assertEqual(catalog(self.temp.name,'90000'),[])

    def test_reminder_keeps_seven_round_boundary(self):
        periodic_guidance.initialize(self.db)
        self.db.execute("INSERT INTO fishbot_guidance_cadence VALUES('self',6)")
        self.assertIsNone(periodic_guidance.view(self.db,'self'))
        self.db.execute("UPDATE fishbot_guidance_cadence SET completed=7")
        self.assertIn('根据情形自然使用收藏或平台表情包，不必每次都发。',periodic_guidance.view(self.db,'self'))
        self.assertEqual(periodic_guidance.INTERVAL,7)

if __name__=='__main__':unittest.main()
