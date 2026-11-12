"""Real admission helpers and extracted production scope-lock/poke stage; no sends."""
import ast,asyncio,sqlite3,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
from native_ingress import admitted,bind,directed_poke
from chat_rhythm import poke
import bot_pipeline

CFG={'groups':[],'behavior':{'groupParticipationGroups':['12345'],'pokeReply':True,'pokeProbability':1}}
RAW={'post_type':'notice','notice_type':'notify','sub_type':'poke','self_id':'99999','user_id':'11111','target_id':'99999'}

def extract(name,namespace,prefix=False):
    tree=ast.parse((ROOT/'adapters/astrbot/behavior.py').read_text(encoding='utf8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    if prefix:
        end=next(i for i,n in enumerate(node.body) if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='internal' for x in n.targets))
        node.body=node.body[:end]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'behavior.py','exec'),namespace)
    return namespace[name]

def event(raw):
    extra={}
    return NS(message_obj=NS(raw_message=raw,message_id='sdk-event',timestamp=time.time()),
        get_sender_id=lambda:str(raw['user_id']),get_self_id=lambda:str(raw['self_id']),
        get_group_id=lambda:str(raw.get('group_id') or ''),get_platform_id=lambda:'qq',
        get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}))

class Policy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.db=sqlite3.connect(':memory:')
    def tearDown(self):self.db.close()

    async def test_group_allowlist_applies_to_all_protocol_kinds_before_conversion(self):
        for kind in ('message','notice','request'):
            calls=[]
            async def original(raw):calls.append('convert');return NS()
            platform=NS(convert_message=original)
            bind(platform,lambda raw:calls.append('retain'),lambda raw:admitted(raw,CFG))
            self.assertIsNone(await platform.convert_message({**RAW,'post_type':kind,'group_id':'54321'}))
            self.assertEqual(calls,[])
        self.assertFalse(admitted({**RAW,'group_id':'12345'},{'behavior':{'groupParticipationGroups':[]}}))
        self.assertFalse(admitted({**RAW,'group_id':'12345'},{**CFG,'groups':['54321']}))
        self.assertTrue(admitted({**RAW,'group_id':'12345'},CFG))
        self.assertTrue(admitted(RAW,{'behavior':{'groupParticipationGroups':[]}}))

    async def test_reaction_is_ignored_but_ordinary_image_and_poke_survive(self):
        self.assertFalse(admitted({**RAW,'notice_type':'group_msg_emoji_like','group_id':'12345'},CFG))
        self.assertTrue(admitted({**RAW,'group_id':'12345'},CFG))
        self.assertTrue(admitted({'post_type':'message','group_id':'12345','message':[{'type':'image','data':{}}]},CFG))

    async def test_private_and_group_program_poke_default_has_cooldown_and_no_ai(self):
        for group in ('','12345'):
            calls=[];ev=event({**RAW,**({'group_id':group} if group else {})})
            async def send(**params):calls.append(params)
            ev.bot=NS(send_poke=send)
            self.assertTrue(await poke(ev,CFG,self.db,lambda:True,now=100))
            self.assertTrue(await poke(ev,CFG,self.db,lambda:True,now=101))
            self.assertEqual(len(calls),1);self.assertFalse(ev.get_extra('_hosted_poke_context'))
            self.assertFalse(bot_pipeline.BotPipeline(self.db).persist_input(ev,{'tenantId':'t','botId':'b','generation':1,'revision':1}))

    async def test_opt_in_poke_never_program_sends_and_other_notices_never_opt_in(self):
        cfg={**CFG,'behavior':{**CFG['behavior'],'pokeIntoAiContext':True}}
        for group in ('','12345'):
            ev=event({**RAW,**({'group_id':group} if group else {})})
            self.assertFalse(await poke(ev,cfg,self.db,lambda:True))
            self.assertTrue(ev.get_extra('_hosted_poke_context'))
        for raw in ({**RAW,'notice_type':'group_msg_emoji_like'}, {**RAW,'target_id':'22222'},
                {**RAW,'sub_type':'input_status'}, {**RAW,'group_id':'54321'}):
            ev=event(raw);self.assertTrue(await poke(ev,cfg,self.db,lambda:True))
            self.assertFalse(ev.get_extra('_hosted_poke_context'))
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='poke_cooldown'").fetchone())

    async def test_opt_in_busy_scope_waits_existing_lock_and_is_concise_protocol_event(self):
        cfg={**CFG,'behavior':{**CFG['behavior'],'pokeIntoAiContext':True}}
        st=NS(scope_turns={},db=self.db)
        st.acquire_scope=extract('acquire_scope',{'asyncio':asyncio}).__get__(st)
        st.release_scope=extract('release_scope',{}).__get__(st)
        self.assertTrue(await st.acquire_scope('group:12345',lambda:True))
        ev=event({**RAW,'group_id':'12345'})
        run=extract('execute',{'setting':lambda e:{'config':cfg},'current':lambda e:True,'store':lambda *a:st,
            'time':time,'poke':poke,'asyncio':asyncio,'Plain':lambda text:NS(text=text),
            'delivery_current':lambda e:True,'bot_pipeline':bot_pipeline},prefix=True)
        task=asyncio.create_task(run(None,ev));await asyncio.sleep(0)
        self.assertFalse(task.done());self.assertIsNone(ev.get_extra('_hosted_scope_owner'))
        st.release_scope('group:12345');await task
        self.assertEqual(ev.get_extra('_hosted_scope_owner'),(st,'group:12345'))
        self.assertIn('协议通知，不是文字发言',ev.message_obj.message[0].text)
        self.assertNotIn('target_id',ev.message_str)
        self.assertEqual(ev.message_obj.message_id,'sdk-event');self.assertEqual(ev.message_obj.raw_message,{**RAW,'group_id':'12345'})
        ev.set_extra('_hosted_scope_continuation',True)
        await asyncio.wait_for(run(None,ev),.5)
        self.assertEqual(st.scope_turns['group:12345']['users'],1)
        st.release_scope('group:12345');self.assertEqual(st.scope_turns,{})

if __name__=='__main__':unittest.main()
