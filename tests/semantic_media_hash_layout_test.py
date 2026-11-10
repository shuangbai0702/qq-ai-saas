"""Linux QQ cache layout is authorized only inside the current bot profile."""
import sys,unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
import semantic_media_test as fixtures


class HashedProfileOwnership(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.ProtocolOwnership.setUp
    tearDown=fixtures.ProtocolOwnership.tearDown
    bind=fixtures.ProtocolOwnership.bind
    release=fixtures.ProtocolOwnership.release
    async def test_hashed_profile_exact_image_reply_is_owned_and_released(self):
        file=self.root/'qq/profile/nt_qq_2ab4af4826637e5f804272a1bd90bb29/nt_data/Pic/2026-11/Ori/image.png'
        file.parent.mkdir(parents=True);file.write_bytes(self.bytes)
        self.event.bot.get_image.return_value={'file':str(file)}
        await self.bind()
        self.assertEqual(self.release()['deleted'],1)
        self.assertFalse(file.exists());self.assertTrue(self.file.exists())

    async def test_hash_layout_cannot_authorize_foreign_bot_or_arbitrary_prefix(self):
        for tail in ('other-bot/qq/profile/nt_qq_2ab4af4826637e5f804272a1bd90bb29/nt_data/Pic/image.png',
                     'qq/profile/nt_qq_not-an-account/nt_data/Pic/image.png',
                     'qq/profile/nt_qq_2ab4af4826637e5f804272a1bd90bb29/nt_db/image.png'):
            file=self.root/tail;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(self.bytes)
            self.event.bot.get_image.return_value={'file':str(file)}
            self.extras['_hosted_media_protocol_checked']=False
            await self.bind()
            self.assertEqual(self.db.execute('SELECT count(*) FROM semantic_media_owned').fetchone()[0],0)
            self.assertTrue(file.exists())
            self.assertEqual(self.release()['protocol_unresolved'],1)


if __name__=='__main__':unittest.main()
