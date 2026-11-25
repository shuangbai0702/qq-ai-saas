import copy,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from character_cards import messages,identity,sha,project,layout,apply_user_cards,validate
from context_protocol import persona_reference
from context_foundation import arrange

def card(id='one',enabled=True):return dict(id=id,name='示例',kind='speech',content='正文😀',enabled=enabled)

class CardsTest(unittest.TestCase):
 def test_sdk_persona_system_card_edits_preserve_exact_slot_and_all_history(self):
  from readonly_journal import system_head,ContextPrefixViolation
  from web_base import SDK_PERSONA_MARKER,SDK_PERSONA_XML_MARKER
  note={'role':'user','content':'[FishBot 上下文固定基础区 v1]\n'+json.dumps({'region':'core_note','core_note':{'text':'owned note'}},ensure_ascii=False,separators=(',',':'))}
  tail=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,AAAA'}}]},
        {'role':'assistant','tool_calls':[{'id':'x','function':{'name':'test','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'x','content':'unchanged receipt'},{'role':'assistant','content':'unchanged reply'}]
  for marker,ending in [(SDK_PERSONA_MARKER,'\n'),(SDK_PERSONA_XML_MARKER,'\n</persona_instructions>')]:
   platform={'role':'system','content':'UNCHANGED PLATFORM'}
   persona={'role':'system','content':'SDK SAFETY\n'+marker+'owned persona'+ending+'\nSDK TOOLS'}
   for with_note in [False,True]:
    original=SimpleNamespace(base=[platform,persona,*tail],wire=[platform,persona,*([note] if with_note else []),*tail],state={'foundation_core_note':{'text':'owned note'}} if with_note else {})
    snapshot=original;origins=[sha([])]
    for cards in [[card(str(i)) for i in range(5)],[card('4')],[card('4',False)],[],[card('new')]]:
     origins.append(sha(messages(cards)));change={'cards':cards,'persona':'owned persona','proof':{'permitId':'7','target':identity(cards),'origins':origins}}
     snapshot=project(snapshot,change)
     for key in ['base','wire']:
      wire=getattr(snapshot,key);expected=[platform,*([note] if with_note and key=='wire' else []),*messages(cards),persona]
      self.assertEqual(wire[:system_head(wire)],expected);self.assertEqual(wire[system_head(wire):],tail)
      self.assertEqual(wire.count(persona),1)
     self.assertIs(project(snapshot,change),snapshot)
    for change in [{'cards':[card()],'proof':{'permitId':'7','target':identity([card()]),'origins':[sha([])]}},
                   {'cards':[card()],'persona':'unproved persona','proof':{'permitId':'7','target':identity([card()]),'origins':[sha([])]}}]:
     with self.assertRaises(ContextPrefixViolation):project(original,change)
    self.assertEqual(original.base,[platform,persona,*tail]);self.assertEqual(original.wire,[platform,persona,*([note] if with_note else []),*tail])

 def test_pending_persona_and_cards_edits_share_authoritative_config_provenance(self):
  from web_base import sha as persona_sha,persona_view
  from readonly_journal import ContextPrefixViolation
  sdk={'role':'system','content':'SDK\n<persona_instructions>\nold persona\n</persona_instructions>\nTOOLS'}
  previous=[{'role':'system','content':'PLATFORM'},sdk,{'role':'user','content':'exact history'}]
  snapshot=SimpleNamespace(base=previous,wire=previous,state={})
  proof={'target':persona_sha('new persona'),'origins':[persona_sha('old persona')]}
  change={'cards':[card()],'persona':'new persona','persona_proof':proof,'proof':{'permitId':'7','target':identity([card()]),'origins':[sha([])]}}
  projected=project(snapshot,change)
  self.assertEqual(projected.base[2],sdk) # Card projection never edits Persona.
  changed=persona_view(projected.base,projected.base,proof,'new persona')
  self.assertEqual(changed[2]['content'],sdk['content'].replace('old persona','new persona'))
  self.assertEqual(changed[-1],previous[-1])
  for invalid in [None,{**proof,'target':persona_sha('other')},{**proof,'origins':[]}]:
   with self.assertRaises(ContextPrefixViolation):project(snapshot,{**change,'persona_proof':invalid})

 def test_missing_persona_requires_its_own_existing_web_save_proof(self):
  from web_base import sha as persona_sha
  from readonly_journal import ContextPrefixViolation
  platform={'role':'system','content':'[FishBot 平台基础规则]\nPLATFORM'}
  tail=[{'role':'user','content':'exact history'}]
  snapshot=SimpleNamespace(base=[platform,*tail],wire=[platform,*tail],state={})
  change={'cards':[card()],'persona':'PERSONA','proof':{'permitId':'7','target':identity([card()]),'origins':[sha([]),sha(messages([card()]))]}}
  untouched=copy.deepcopy(snapshot)
  cards_only=project(snapshot,change)
  self.assertEqual(cards_only.wire,[platform,*messages([card()]),*tail])
  self.assertEqual(cards_only.base,[platform,*messages([card()]),*tail])
  self.assertEqual(snapshot.__dict__,untouched.__dict__)
  self.assertIs(project(cards_only,change),cards_only)
  self.assertFalse(any(m.get('content','').startswith('[FishBot 网页 Persona 参考') for m in cards_only.wire))
  change['persona_proof']={'target':persona_sha('PERSONA'),'origins':[persona_sha('PERSONA')]}
  change['proof']['origins'].append(sha(messages([card()])))
  projected=project(snapshot,change)
  self.assertEqual(projected.wire,[platform,*messages([card()]),persona_reference('PERSONA'),*tail])
  self.assertIs(project(projected,change),projected)
  with self.assertRaises(ContextPrefixViolation):layout([{'role':'system','content':'unknown platform'},*tail],[card()],persona='PERSONA',persona_proof=change['persona_proof'])

 def test_card_preflight_is_readonly_and_rejects_unproved_origins(self):
  import sqlite3
  from readonly_journal import ReadonlyJournal,ContextPrefixViolation
  from context_protocol import READONLY_CURRENT
  from web_cognition import validate_cards
  with sqlite3.connect(':memory:') as db:
   journal=ReadonlyJournal(db,'bot_shared')
   base=[{'role':'system','content':'[FishBot 平台基础规则]\nPLATFORM'}, {'role':'user','content':'exact history'}]
   first=journal.prepare(base+[{'role':'user','content':READONLY_CURRENT+'\n{}'}],1);self.assertTrue(journal.commit(first))
   old=db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0];changes=db.total_changes
   setting={'config':{'characterCards':[card()],'systemPrompt':'PERSONA'},'characterCardsEdit':{'permitId':'7','target':identity([card()]),'origins':[sha([])]}}
   validate_cards(db,setting)
   self.assertEqual(db.total_changes,changes);self.assertEqual(journal.last.wire,first.wire)
   self.assertEqual(db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],old)
   setting['characterCardsEdit']['origins']=[sha(messages([card('foreign')]))]
   with self.assertRaises(ContextPrefixViolation):validate_cards(db,setting)
   with self.assertRaises(ContextPrefixViolation):validate_cards(db,setting,journals=[journal])
   self.assertEqual(db.total_changes,changes)

 def test_sdk_card_update_commits_locally_and_still_requires_system_permit(self):
  import sqlite3
  from readonly_journal import ReadonlyJournal
  from context_protocol import READONLY_CURRENT
  from system_contract import SystemContract,SystemContractViolation
  with sqlite3.connect(':memory:') as db:
   journal=ReadonlyJournal(db,'bot_shared');guard=SystemContract(db)
   packet={'role':'user','content':READONLY_CURRENT+'\n{}'}
   base=[{'role':'system','content':'PLATFORM'},{'role':'system','content':'SDK\n<persona_instructions>\nPERSONA\n</persona_instructions>\nTOOLS'},{'role':'user','content':'original input'}]
   first=journal.prepare(base+[packet],1);self.assertTrue(journal.commit(first))
   guard.settle(guard.reserve(first.wire,None,'initial',1,scope='bot_shared'),True,2)
   change={'cards':[card()],'persona':'PERSONA','proof':{'permitId':'7','target':identity([card()]),'origins':[sha([])]}}
   projected=project(first,change)
   candidate=journal.prepare(projected.base+[{'role':'user','content':'next input'},packet],2,cognition_edit={'character_cards':change})
   self.assertEqual(candidate.turns,first.turns+1);self.assertIsNone(candidate.reconstruction_permit)
   with self.assertRaises(SystemContractViolation):guard.reserve(candidate.wire,None,'untrusted',3,scope='bot_shared')
   reserved=guard.reserve(candidate.wire,'7','authorized',4,scope='bot_shared')
   self.assertTrue(journal.commit(candidate));self.assertTrue(guard.settle(reserved,True,5))
   restored=ReadonlyJournal(db,'bot_shared').last
   self.assertEqual(restored.wire,candidate.wire);self.assertEqual(restored.base[-2:],candidate.base[-2:])
   self.assertEqual(restored.wire[2],base[1]);self.assertEqual(restored.base[2],base[1])
   self.assertEqual(restored.wire[3:len(first.wire)+1],first.wire[2:])

 def test_utf16_matches_browser(self):
  validate([{**card(),'content':'😀'*1250}])
  validate([{**card(),'content':'\u001c'}])
  for value in ('\ufeff','\u0000','\ud800'):
   with self.assertRaises(ValueError):validate([{**card(),'content':value}])
  with self.assertRaises(ValueError):validate([{**card(),'content':'😀'*1251}])
 def test_precise_legacy_projection_and_repeated_edits_preserve_every_other_byte(self):
  note={'text':'原note','source_scopes':['bot_shared']};state={'memory_foundation':{},'foundation_core_note':note}
  base=[{'role':'system','content':'PLATFORM'},persona_reference('PERSONA')]
  tail=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,AAAA'}}]}, {'role':'assistant','tool_calls':[{'id':'x','function':{'name':'test','arguments':'{}'}}]}, {'role':'tool','tool_call_id':'x','content':'REAL_RECEIPT'}]
  legacy=arrange(base,state)
  notes=[m for m in legacy if m.get('role')=='user' and m.get('content','').startswith('[FishBot 上下文固定基础区 v1]')]
  legacy=base+notes
  snapshot=SimpleNamespace(base=base+tail,wire=legacy+tail,state=state)
  original=copy.deepcopy(snapshot);origins=[sha([])]
  for cards in [[card(str(i)) for i in range(5)],[card('4'),card('0')],[card('4',False)],[],[card('new')]]:
   origins.append(sha(messages(cards)));change={'cards':cards,'proof':{'permitId':'7','target':identity(cards),'origins':origins}}
   updated=project(snapshot,change);wire=updated.wire
   self.assertEqual(wire[-3:],tail);self.assertEqual(updated.base[-3:],tail);self.assertEqual(wire[0],base[0]);self.assertEqual(wire[1]['role'],'user');self.assertEqual(json.loads(wire[1]['content'].split('\n',1)[1])['core_note'],note)
   self.assertEqual(wire[2:2+len(messages(cards))],messages(cards));self.assertEqual(wire[2+len(messages(cards))],base[1]);self.assertEqual(project(updated,change).__dict__,updated.__dict__)
   snapshot=updated
  self.assertEqual(original.wire,legacy+tail)
 def test_provenance_and_unrelated_config_rejected(self):
  current={'botId':'a','tenantId':'t','generation':1,'revision':1,'config':{'characterCards':[],'systemPrompt':'P'}}
  cfg={**current['config'],'characterCards':[card()]};proof={'permitId':'7','target':identity([card()]),'origins':[sha([]),sha(messages([card()]))]}
  body={**current,'revision':2,'config':cfg,'characterCardsEdit':proof}
  result=apply_user_cards(current,body);self.assertEqual(current['config']['characterCards'],[]);self.assertEqual(result['config'],cfg)
  for patch in [{'generation':2},{'tenantId':'other'},{'revision':0},{'config':{**cfg,'systemPrompt':'changed'}},{'characterCardsEdit':{**proof,'target':'bad'}},{'characterCardsEdit':{**proof,'origins':[]}},{'characterCardsEdit':{**proof,'origins':[sha(messages([card()]))]}}]:
   with self.assertRaises(ValueError):apply_user_cards(current,{**body,**patch})
  snapshot=SimpleNamespace(base=[{'role':'system','content':'PLATFORM'},*messages([card('foreign')]),persona_reference('P')],wire=[],state={})
  with self.assertRaises(RuntimeError):project(snapshot,{'cards':[card()],'proof':proof})
 def test_no_cards_no_note_no_placeholders(self):
  base=[{'role':'system','content':'P'},persona_reference('persona')]
  for cards in [[],[card(enabled=False)]]:self.assertEqual(layout(base,cards),base)
  residual=persona_reference('SDK residual instructions')
  self.assertEqual(layout(base+[residual],[card()]),[base[0],*messages([card()]),base[1],residual])

 def test_lawful_checkpoint_replaces_owned_note_once_and_keeps_cards(self):
  from character_cards import is_note
  base=[{'role':'system','content':'PLATFORM'},*messages([card()]),persona_reference('PERSONA')]
  old={'memory_foundation':{},'foundation_core_note':{'text':'old'}}
  first=arrange(base,old)
  current={'memory_foundation':{},'foundation_core_note':{'text':'current'}}
  updated=arrange(first,current)
  self.assertEqual(sum(is_note(m) for m in updated),1)
  self.assertEqual(json.loads(updated[1]['content'].split('\n',1)[1])['core_note'],current['foundation_core_note'])
  self.assertEqual(updated[2:3],messages([card()]))
  self.assertEqual(arrange(updated,current),updated)
  self.assertEqual(arrange(updated,{'memory_foundation':{},'foundation_core_note':{}}),base)
  with self.assertRaises(RuntimeError):project(SimpleNamespace(base=base,wire=first,state={}),{'cards':[card()],'proof':{'permitId':'7','target':identity([card()]),'origins':[sha(messages([card()]))]}})

 def test_card_projection_storage_failure_preserves_original_journal(self):
  import sqlite3
  from readonly_journal import ReadonlyJournal
  from context_protocol import READONLY_CURRENT
  with sqlite3.connect(':memory:') as db:
   journal=ReadonlyJournal(db,'bot_shared');packet={'role':'user','content':READONLY_CURRENT+'\n{}'}
   base=[{'role':'system','content':'PLATFORM'},persona_reference('PERSONA'),{'role':'user','content':'original input'}]
   first=journal.prepare(base+[packet],1);self.assertTrue(journal.commit(first))
   old=db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0]
   change={'cards':[card()],'proof':{'permitId':'7','target':identity([card()]),'origins':[sha([]),sha(messages([card()]))]}}
   projected=project(first,change)
   candidate=journal.prepare(projected.base+[{'role':'user','content':'next input'},packet],2,cognition_edit={'character_cards':change})
   self.assertIsNone(candidate.reconstruction_permit);self.assertEqual(candidate.turns,first.turns+1)
   db.execute("CREATE TRIGGER reject_snapshot BEFORE INSERT ON fishbot_readonly_journals BEGIN SELECT RAISE(ABORT,'fixture disk failure'); END");db.commit()
   with self.assertRaises(sqlite3.IntegrityError):journal.commit(candidate)
   self.assertEqual(db.execute('SELECT payload FROM fishbot_readonly_journals').fetchone()[0],old)
   self.assertEqual(ReadonlyJournal(db,'bot_shared').last.wire,first.wire)

class CardsHttpWireTest(unittest.IsolatedAsyncioTestCase):
 async def test_real_sdk_serialization_preserves_media_tools_and_owned_regions(self):
  import base64,io,os,tempfile
  from unittest.mock import patch
  root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'references/astrbot'))
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'ASTRBOT_ROOT':temp}):
   import runtime_bootstrap
   runtime_bootstrap.initialize_runtime_bootstrap()
   if 'astrbot.core.provider.entities' not in sys.modules:
    from slim_bootstrap import install
    install()
   import httpx
   from PIL import Image
   from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
   captured=[]
   async def receive(request):
    self.assertEqual(str(request.url),'https://cards-fixture.invalid/v1/chat/completions')
    captured.append(json.loads(request.content))
    return httpx.Response(200,json={'id':'fixture','model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}]})
   with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda owner,cfg:httpx.AsyncClient(transport=httpx.MockTransport(receive))):
    provider=ProviderOpenAIOfficial({'id':'cards-wire','type':'openai_chat_completion','model':'fixture','key':['synthetic'],'api_base':'https://cards-fixture.invalid/v1'}, {})
   png=io.BytesIO();Image.new('RGB',(2,2),(20,40,80)).save(png,format='PNG')
   tail=[{'role':'user','content':[{'type':'text','text':'historical image'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png.getvalue()).decode(),'detail':'low'}}]},
         {'role':'assistant','content':None,'tool_calls':[{'id':'read-1','type':'function','function':{'name':'inspect_context','arguments':'{}'}}]},
         {'role':'tool','tool_call_id':'read-1','content':'original receipt'}, {'role':'assistant','content':'original reply'}, {'role':'user','content':'next request'}]
   bases=[[{'role':'system','content':'PLATFORM'},persona_reference('PERSONA')],
          [{'role':'system','content':'PLATFORM'},{'role':'system','content':'SDK SAFETY\n<persona_instructions>\nPERSONA\n</persona_instructions>\nSDK TOOLS'}]]
   try:
    for base,with_note in [(b,n) for b in bases for n in (False,True)]:
     state={'memory_foundation':{},'foundation_core_note':{'text':'Core Note'}} if with_note else {}
     initial=base
     # Exercise the committed pre-feature Persona -> Note layout over HTTP.
     if with_note:initial=base+[arrange(bases[0],state)[1]]
     snapshot=SimpleNamespace(base=base+tail,wire=initial+tail,state=state)
     await provider.text_chat(contexts=snapshot.wire,prompt=None,func_tool=None)
     original=captured[-1]['messages'][-len(tail):];origins=[sha([])]
     for cards in [[card(str(i)) for i in range(5)],[card('4'),card('0')],[{**card('4'),'content':'edited'}],[card('4',False)],[],[card('new')]]:
      origins.append(sha(messages(cards)));change={'cards':cards,'proof':{'permitId':'7','target':identity(cards),'origins':origins},'persona':'PERSONA'}
      snapshot=project(snapshot,change)
      await provider.text_chat(contexts=snapshot.wire,prompt=None,func_tool=None)
      wire=captured[-1]['messages'];self.assertEqual(wire[-len(tail):],original)
      self.assertEqual([m for m in wire if m.get('role')=='system' and m.get('content','').startswith('[FishBot 角色卡] ')],messages(cards))
      self.assertEqual(wire[0],base[0]);self.assertEqual(wire[1+int(with_note):1+int(with_note)+len(messages(cards))],messages(cards))
      self.assertEqual(wire[1+int(with_note)+len(messages(cards))],base[1])
   finally:await provider.client.close()

if __name__=='__main__':unittest.main()
