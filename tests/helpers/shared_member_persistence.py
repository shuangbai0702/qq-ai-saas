"""Deterministic per-bot Work/Journal fixture used by shared lifecycle tests."""
import hashlib,json,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root/'adapters/astrbot'),str(root/'tests')]
from behavior import BehaviorStore
from bot_pipeline_test import event
from readonly_journal import ReadonlyJournal
from context_protocol import SOURCE,READONLY_CURRENT,model_json
from context_transport import EVENT_CONTEXT_CURRENT

def message(scope,event='same',who='12345678',text='hello'):
    return {'role':'user','content':SOURCE+model_json({'scope':scope,'event_id':event,'sender_id':who,'at':time.time()})+'\n'+text}

def request(m):
    return [{'role':'system','content':'fixed bot identity'},
            {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})},
            {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{'identity':{'scope':'current'}},'policy':'fixed','currentGuidance':'@ permits NO_REPLY','meaning':'platform'})},m]

file,bot,mode=sys.argv[1:]
store=BehaviorStore(Path(file));journal=ReadonlyJournal(store.db,'bot_shared')
try:
    if mode=='seed':
        media=message('private:12345678','old-media')
        media['content']=[{'type':'text','text':media['content']},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]
        p=journal.prepare(request(media),('bot',),strict=True,reconstruction_scope='bot_shared')
        assert journal.stage_candidate(p) and journal.commit(p)
        work=store.pipeline.bind(event(),{'botId':bot,'generation':1})
        store.pipeline.sending(work);work.stop('TimeoutError','error');store.pipeline.finish(work,{})
    duplicate=store.pipeline.bind(event(),{'botId':bot,'generation':2})
    assert duplicate.status=='duplicate'
    rows=store.db.execute('SELECT scope,event,subject,status,attempted,confirmed FROM pipeline_events ORDER BY scope,event').fetchall()
    assert rows and rows[0][-3:]==('unknown',1,0)
    print(model_json({'events':rows,'wireSha':hashlib.sha256(model_json(journal.last.wire).encode()).hexdigest(),'duplicate':duplicate.status}))
finally:store.close()
